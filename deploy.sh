#!/bin/bash

# 한입 비트코인 서비스 허브 배포 스크립트
# /var/www/html/에 파일들을 복사합니다

set -e  # 에러 발생시 스크립트 중단

# 색상 출력을 위한 변수
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}한입 비트코인 서비스 허브 배포를 시작합니다...${NC}"

# 배포할 파일들
FILES=(
    "index.html"
    "thumbnail.png"
    "favicon.png"
)

# /var/www/html/ 디렉토리 확인
if [ ! -d "/var/www/html" ]; then
    echo -e "${YELLOW}/var/www/html 디렉토리가 없습니다. 생성합니다...${NC}"
    sudo mkdir -p /var/www/html
fi

# 파일 복사
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "  - $file 복사 중..."
        sudo cp "$file" /var/www/html/
    else
        echo -e "${YELLOW}  - 경고: $file 파일을 찾을 수 없습니다.${NC}"
    fi
done

# 파일 권한 설정 (웹 서버가 읽을 수 있도록)
echo -e "\n파일 권한 설정 중..."
sudo chown -R www-data:www-data /var/www/html/ 2>/dev/null || sudo chown -R $(whoami):$(whoami) /var/www/html/
sudo chmod -R 755 /var/www/html/

echo -e "\n${GREEN}✓ 배포가 완료되었습니다!${NC}"
echo -e "파일 위치: /var/www/html/"
echo -e "\n배포된 파일 목록:"
ls -lh /var/www/html/
