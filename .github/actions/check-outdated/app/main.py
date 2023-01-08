import logging
from pathlib import Path

import httpx

from github import Github

from pydantic import BaseModel, BaseSettings, SecretStr, validator
from pydantic.generics import GenericModel
from datetime import datetime
from typing import Generic, TypeVar, IO


class Settings(BaseSettings):
    input_token: SecretStr
    input_package: str
    input_repository: str
    github_workspace: Path
    github_output: Path
    httpx_timeout: int = 30


github_graphql_url = "https://api.github.com/graphql"

releases_query = """
query Q($owner: String!, $name: String!){
  repository(name: $name, owner: $owner, followRenames: true) {
    latestRelease {
      createdAt
      id
      name
      tagName
      publishedAt
    }
  }
}
"""


class ReleaseNode(BaseModel):
    id: str
    name: str
    tagName: str
    publishedAt: datetime


class ReleaseRepository(BaseModel):
    latestRelease: ReleaseNode


class ReleaseRepositoryResponse(BaseModel):
    repository: ReleaseRepository


DataT = TypeVar("DataT")


class ResponseModel(GenericModel, Generic[DataT]):
    data: DataT


class Package(BaseModel):
    path: Path
    pkgbuild_path: Path
    srcinfo_path: Path

    @validator("pkgbuild_path", "srcinfo_path")
    def _validate_paths(cls, v: Path):
        if not v.exists():
            raise FileNotFoundError(str(v))
        return v

    @classmethod
    def from_path(cls, path: Path):
        return Package(
            path=path, pkgbuild_path=path / "PKGBUILD", srcinfo_path=path / ".SRCINFO"
        )

    def find_current_version(self) -> str | None:
        srcinfo = self.srcinfo_path.read_text()
        for line in srcinfo.splitlines():
            l = line.strip()
            if "=" not in l:
                continue
            key, value = l.split(" = ")
            if key.strip() == "pkgver":
                return value.strip()


def get_graphql_response(*, settings: Settings, query: str, **variables):
    headers = {"Authorization": f"token {settings.input_token.get_secret_value()}"}
    response = httpx.post(
        github_graphql_url,
        headers=headers,
        timeout=settings.httpx_timeout,
        json={"query": query, "variables": variables, "operationName": "Q"},
    )
    if response.status_code != 200:
        logging.error(f"Response was not 200, vars: {variables}")
        logging.error(response.text)
        raise RuntimeError(response.text)
    data = response.json()
    return data


def set_output(settings: Settings, **values) -> None:
    with settings.github_output.open("a") as f:
        for key, value in values.items():
            cmd = f"{key}={value}\n"
            f.write(cmd)


def main():
    settings = Settings()
    logging.info("settings: %s", settings.json())
    pkg = Package.from_path(settings.github_workspace / settings.input_package)
    logging.info("package: %s", pkg)
    repo_owner, repo_name = settings.input_repository.split("/")
    data = get_graphql_response(
        settings=settings, query=releases_query, name=repo_name, owner=repo_owner
    )
    releases = ResponseModel[ReleaseRepositoryResponse].parse_obj(data)
    logging.info("recv releases: %s", releases)

    latest_version = releases.data.repository.latestRelease.tagName
    current_version = pkg.find_current_version()
    if not current_version:
        raise RuntimeError("Failed to determine current version for:", pkg.json())
    current_version = current_version.lstrip("v")
    latest_version = latest_version.lstrip("v")
    is_outdated = current_version != latest_version
    results = {
        "current-version": current_version,
        "latest-version": latest_version,
        "outdated": "true" if is_outdated else "false",
    }
    logging.info("results for %s: (%s)", settings.input_package, results)
    set_output(settings, **results)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
