FROM python:3.11
RUN pip --no-cache-dir install httpx PyGithub pydantic
COPY ./app /app
CMD ["python", "/app/main.py"]

