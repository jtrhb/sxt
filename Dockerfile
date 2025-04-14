FROM python:3.11

ARG GIT_TOKEN

WORKDIR /sxt

COPY . .

RUN sed -i "s#https://github.com#https://${GIT_TOKEN}@github.com#g" .gitmodules && \
  git submodule sync && \
  git submodule update --init --recursive

RUN pip install -r requirements.txt

EXPOSE 3333

RUN chmod +x start.sh

CMD ["./start.sh"]