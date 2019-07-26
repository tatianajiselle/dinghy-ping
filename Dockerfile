FROM kennethreitz/pipenv

EXPOSE 80/tcp 8000/tcp

COPY . /app

WORKDIR /app/dinghy-ping/services

CMD python3 api.py
