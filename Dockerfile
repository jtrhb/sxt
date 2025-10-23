FROM python:3.11

WORKDIR /sxt

COPY . .

RUN pip install -r requirements.txt

RUN chmod +x start.sh

CMD ["./start.sh"]