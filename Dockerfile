FROM python:2.7-alpine
COPY setup.py requirements.txt README.md /usr/src/hpilo_exporter/
COPY src /usr/src/hpilo_exporter/src
RUN pip install -e /usr/src/hpilo_exporter
ENTRYPOINT ["hpilo-exporter"]
EXPOSE 9416
