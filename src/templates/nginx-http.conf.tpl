events {
    worker_connections 1024;
}

http {
    # This server block listens on the internal Docker network on port 80.
    # It assumes an external reverse proxy is handling SSL termination and public traffic.
    server {
        listen 80;

        # The server name can be a wildcard or the specific internal hostname,
        # as the external proxy will be responsible for routing the correct public domain.
        server_name _;

        # Custom error pages
        error_page 502 503 504 /maintenance.html;
        location = /maintenance.html {
            root /usr/share/nginx/html;
            internal;
        }

        location / {
            proxy_pass http://opal:8080/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Forwarded-Port $server_port;
            
            # WebSocket support
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
} 