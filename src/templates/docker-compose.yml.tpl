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
      - MONGO_HOST=mongo
      - MONGO_PORT=27017
      - OPAL_ADMINISTRATOR_PASSWORD=${OPAL_ADMIN_PASSWORD}
      - OPAL_PROXY_SECURE=true
      - OPAL_PROXY_HOST=${OPAL_HOSTNAME}
      - OPAL_PROXY_PORT=${OPAL_EXTERNAL_PORT}
      - ROCK_HOSTS=${OPAL_ROCK_HOSTS}
      - ROCK_DEFAULT_ADMINISTRATOR_USERNAME=administrator
      - ROCK_DEFAULT_ADMINISTRATOR_PASSWORD=password
      - ROCK_DEFAULT_MANAGER_USERNAME=manager
      - ROCK_DEFAULT_MANAGER_PASSWORD=password
      - ROCK_DEFAULT_USER_USERNAME=user
      - ROCK_DEFAULT_USER_PASSWORD=password
    networks:
      - opal-net

  nginx:
    image: nginx:latest
    container_name: ${PROJECT_NAME}-nginx
    restart: always
    ports:
      - "${OPAL_EXTERNAL_PORT}:443"
      - "80:80"
    volumes:
      - ./data/nginx/conf/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./data/nginx/certs:/etc/nginx/certs:ro
      - ./data/letsencrypt/www:/var/www/certbot:ro
      - ./data/letsencrypt/conf:/etc/letsencrypt
    depends_on:
      - opal
    networks:
      - opal-net

  certbot:
    image: certbot/certbot
    container_name: ${PROJECT_NAME}-certbot
    volumes:
      - ./data/letsencrypt/www:/var/www/certbot:rw
      - ./data/letsencrypt/conf:/etc/letsencrypt

  # Rock profiles will be added here dynamically

networks:
  opal-net:
    driver: bridge

volumes:
  opal_mongo_data: 