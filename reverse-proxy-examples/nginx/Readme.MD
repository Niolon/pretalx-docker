# NGINX Config example

Your config could look like this:

```
server {
    listen 80 default_server;
    listen [::]:80 ipv6only=on default_server;
    server_name pretalx.mydomain.com;
    return 301 https://pretalx.mydomain.com$request_uri;
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl ipv6only=on default_server;
    server_name pretalx.mydomain.com;

    ssl_certificate /path/to/cert.chain.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8346/;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Host $http_host;
    }

    # ARC file answers, temporary form uploads and cached exports are private.
    # /arc-files/ goes through location / above for application authorisation.
    # Never expose /data or /data/arc-private with an alias/root directive.
    location ~ ^/media/[^/]+/question_uploads/ {
      return 404;
    }
    location ~ ^/media/(cfp_uploads|cachedfiles)/ {
      return 404;
    }

    location /media/ {
      alias /path/to/pretalx/public/media/;
      types { }
      default_type application/octet-stream;
      add_header Content-Disposition attachment always;
      add_header X-Content-Type-Options nosniff always;
      expires 7d;
      access_log off;
    }

    location /static/ {
      alias /path/to/pretalx/public/static/;
      expires 7d;
      access_log off;
    }

}
```
