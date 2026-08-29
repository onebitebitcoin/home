# 이 앱은 표준 라이브러리만 쓴다. requirements.txt 도, 빌드 단계도 없다.
# 형제 프로젝트(ai-daily-web/backend)와 같은 베이스를 쓴다.
FROM python:3.11-slim

WORKDIR /app
COPY . .

# 공지 DB 는 볼륨에 둔다. 이미지 안에 두면 재배포마다 글이 날아간다.
ENV NOTICE_DB=/data/notices.db
ENV PORT=8899

EXPOSE 8899

CMD ["python3", "server.py"]
