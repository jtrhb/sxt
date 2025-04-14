FROM python:3.11

RUN apt-get update && apt-get install -y git && apt-get clean

WORKDIR /sxt

COPY . .

RUN git submodule update --init --recursive

RUN pip install -r requirements.txt

EXPOSE 3333

RUN chmod +x start.sh

CMD ["./start.sh"]