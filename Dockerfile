FROM python:3.12-alpine3.20
LABEL maintainer="xdream oldlu <xdream@gmail.com>"

RUN apk add --no-cache curl
WORKDIR /app

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

# 
COPY ./app /app/app

# 
CMD ["uvicorn", "app.main:app", "--proxy-headers", "--host", "0.0.0.0", "--port", "8001"]