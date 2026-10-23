# PythonとNuSMVを同じ環境にそろえる．workflowのjobを動かすイメージではない．
# NuSMV is an x86_64 static binary; build/run this image as linux/amd64.
# NuSMVのx86_64バイナリを取得する段階．実行時はCompose側でもlinux/amd64を指定する．
FROM badouralix/nusmv@sha256:96ac90ba3ae63f9423ad26611ac4c6f8774fe383baff73bc3fbc09456246992e AS nusmv
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
# ホストへ依存を入れず，Pythonライブラリもイメージ内へ固定版で導入する．
RUN python3 -m pip install --no-cache-dir PyYAML==6.0.3
COPY --from=nusmv /opt/nusmv /opt/nusmv
ENV PATH="/opt/nusmv/bin:${PATH}" \
    PYTHONPATH=/repo/tools \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /repo
# ソースと評価入力だけを含める．生成済みoutputsはイメージへコピーしない．
COPY tools/ tools/
COPY tests/ tests/
COPY .github/workflows/ .github/workflows/
COPY experiments/public-cases/tanstack/ experiments/public-cases/tanstack/
# 実行ユーザーを非rootにし，結果の書込み先だけを用意する．
RUN groupadd --gid 1000 researcher \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp --shell /bin/bash researcher \
    && mkdir /out && chown 1000:1000 /out
USER researcher
CMD ["bash"]
