# Until Cloud — Cloudflare Containers용 이미지 (빌드 컨텍스트 = 리포지토리 루트)
#   docker build -f deploy/Dockerfile -t until-app .
# 앱 코어는 표준 라이브러리 — openai(라이브 백엔드)·pymupdf(PDF 첨부 파싱)만 설치.
# pymupdf 없으면 eTL 첨부 PDF(과제 원문이 든 경우 多)가 스킵돼 초안이 빈껍데기가 된다.
FROM python:3.13-slim

RUN pip install --no-cache-dir openai pymupdf python-pptx jinja2 fastapi uvicorn python-multipart

WORKDIR /app
COPY until/ until/
# 랜딩(소개) 페이지를 앱에서도 서빙(/about) — 원본 단일 소스는 deploy/landing.
# 스크린샷도 함께 번들 — CSP img-src 'self'라 외부(workers) 이미지는 차단된다.
COPY deploy/landing/public/index.html until/webassets/landing.html
COPY deploy/landing/public/img/*.jpg until/webassets/

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UNTIL_CLOUD=1 \
    PORT=8080

EXPOSE 8080

# 백엔드·키·KV·베타 코드는 런타임 env로 주입(wrangler secret / envVars).
CMD ["sh", "-c", "uvicorn until.asgi:app --host 0.0.0.0 --port ${PORT:-8080}"]
