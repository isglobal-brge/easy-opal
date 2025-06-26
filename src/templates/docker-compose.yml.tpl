services:
  mongo:
    image: mongo:4.4
    container_name: ${PROJECT_NAME}-mongo
    restart: always
    volumes:
      - opal_mongo_data:/data/db
    networks:
      opal-net:
        aliases:
          - mongo

  opal:
    image: obiba/opal:latest
    container_name: ${PROJECT_NAME}-opal
    restart: always
    depends_on:
      - mongo
    volumes:
      - opal_srv_data:/srv
    environment:
      MONGO_HOST: mongo
      MONGO_PORT: 27017
      OPAL_ADMINISTRATOR_PASSWORD: ${OPAL_ADMIN_PASSWORD}
      OPAL_PROXY_SECURE: true
      OPAL_PROXY_HOST: localhost
      OPAL_PROXY_PORT: 443
      CSRF_ALLOWED: "*"
      ROCK_HOSTS: "http://rock:8085"
      ROCK_DEFAULT_ADMINISTRATOR_USERNAME: administrator
      ROCK_DEFAULT_ADMINISTRATOR_PASSWORD: password
      ROCK_DEFAULT_MANAGER_USERNAME: manager
      ROCK_DEFAULT_MANAGER_PASSWORD: password
      ROCK_DEFAULT_USER_USERNAME: user
      ROCK_DEFAULT_USER_PASSWORD: password
    networks:
      opal-net:
        aliases:
          - opal

  nginx:
    image: nginx:latest
    container_name: ${PROJECT_NAME}-nginx
    restart: always
    ports: []
    volumes:
      - ./data/nginx/conf/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./data/nginx/certs:/etc/nginx/certs:ro
      - ./data/nginx/html:/usr/share/nginx/html:ro
      - ./data/letsencrypt/www:/var/www/certbot:ro
      - ./data/letsencrypt/conf:/etc/letsencrypt
    depends_on:
      - opal
    networks:
      opal-net:
        aliases:
          - nginx

  certbot:
    image: certbot/certbot
    container_name: ${PROJECT_NAME}-certbot
    volumes:
      - ./data/letsencrypt/www:/var/www/certbot:rw
      - ./data/letsencrypt/conf:/etc/letsencrypt
    networks:
      opal-net:
        aliases:
          - certbot

  # Rock profiles will be added here dynamically

networks:
  opal-net:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.name: opal-br0
      com.docker.network.bridge.enable_icc: "true"
      com.docker.network.bridge.enable_ip_masquerade: "true"
    ipam:
      driver: default
      config:
        - subnet: 172.20.0.0/16
          gateway: 172.20.0.1

volumes:
  opal_mongo_data:
  opal_srv_data: 