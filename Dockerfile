FROM python:3.11

WORKDIR /sxt

COPY . .

RUN pip install -r requirements.txt

EXPOSE 3333

RUN chmod +x start.sh

CMD ["./start.sh"]