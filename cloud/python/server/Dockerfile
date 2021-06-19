FROM amazonlinux:latest
RUN yum -y install python37 python37-pip
COPY . server
COPY requirements.txt server/requirements.txt
RUN pip3 install -r server/requirements.txt
ENTRYPOINT ["python3", "server/run_hourly.py"]
