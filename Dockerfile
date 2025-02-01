FROM python:3.13.1-bookworm
COPY . /app
WORKDIR /app
RUN pip install pipenv \
    && pipenv sync --system
CMD "gunicorn"