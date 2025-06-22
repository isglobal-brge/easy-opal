version: '3.8'

services:
  mongo:
    image: mongo:4.4
    container_name: ${PROJECT_NAME}-mongo
    restart: always
    volumes:
      - opal_mongo_data:/data/db
    networks:
      - opal-net

  opal:
    image: obiba/opal:latest
    container_name: ${PROJECT_NAME}-opal
    restart: always
    depends_on:
      - mongo
    environment:
      - OPAL_MONGODB_HOST=mongo
      - OPAL_MONGODB_PASSWORD=${OPAL_ADMIN_PASSWORD}
      - OPAL_ADMINISTRATOR_PASSWORD=${OPAL_ADMIN_PASSWORD}
      - OPAL_PROXY_SECURE=true
      - OPAL_PROXY_HOST=${OPAL_HOSTNAME}
      - OPAL_PROXY_PORT=${OPAL_EXTERNAL_PORT}
    networks:
      - opal-net

  nginx:
    image: nginx:latest
    container_name: ${PROJECT_NAME}-nginx
    restart: always
    ports:
      - "${OPAL_EXTERNAL_PORT}:443"
    volumes:
      - ./data/nginx/conf/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./data/nginx/certs:/etc/nginx/certs:ro
    depends_on:
      - opal
    networks:
      - opal-net

  # Rock profiles will be added here dynamically

networks:
  opal-net:
    driver: bridge

volumes:
  opal_mongo_data: 