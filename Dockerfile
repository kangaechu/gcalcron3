FROM python:3.7

RUN apt-get update && \
    apt-get install -y at && \
    rm /etc/localtime && \
    ln -s /usr/share/zoneinfo/Asia/Tokyo /etc/localtime && \
    service atd start && \
    mkdir -p /root/gcalcron

COPY requirements.txt /root/gcalcron/
WORKDIR /root/gcalcron
RUN pip install -r requirements.txt

CMD ["python", "gcalcron.py"]