FROM manimcommunity/manim:v0.20.1@sha256:f18f53f2e4eaf2ea41713437d34363fb3f5cc6008b03fd798676ac0359396c3b
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk ffmpeg \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f
USER 1000:1000
WORKDIR /work
