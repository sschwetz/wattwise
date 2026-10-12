FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postfix libsasl2-modules \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25

ENTRYPOINT ["/entrypoint.sh"]
