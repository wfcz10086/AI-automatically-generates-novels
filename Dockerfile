FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
COPY requirements.txt .
# 服务端不需要 playwright（那是测试用），单独装可省一大截镜像
RUN grep -v playwright requirements.txt > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt

COPY server/ ./server/
COPY packs/ ./packs/
COPY web/ ./web/
COPY config/ ./config/
COPY run_novel.py ./

ENV NOVEL_PORT=60001 \
    NOVEL_SEARCH_URL=http://searxng:8080
EXPOSE 60001
CMD ["python3", "server/app.py"]
