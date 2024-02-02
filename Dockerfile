FROM centos/python-38-centos7

COPY requirements.txt ./
RUN pip3 install -r requirements.txt

COPY run.sh bepelias.py prepare_best_files.py ./

CMD ["./run.sh", "run"]

EXPOSE 4001