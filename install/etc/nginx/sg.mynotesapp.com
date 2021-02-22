upstream mynotes  {
    server 127.0.0.1:8081 weight=1;
    server 127.0.0.1:8082 weight=1;
    }
    
server {
    server_name sg.mynotesapp.com;
    listen 80;

    access_log /home/inexika/mynotes/log/nginx/access.log;
    error_log  /home/inexika/mynotes/log/nginx/error.log notice;

    client_max_body_size 32m;
    proxy_buffering off;
    proxy_read_timeout 300s;

    # Tengine feature - http://tengine.taobao.org/document/http_core.html
    proxy_request_buffering off;

    # Root of the server is permanently redirected to mynotesapp.com (301 redirect)
    location = / {
       rewrite ^ http://www.mynotesapp.com/ permanent;
    }
    location ~ /stats {
        root /home/inexika/mynotes/www/;
        index index.html;
    }

    # All other requests except those 
    # started with /event and /subscriber and mache "port number" regex
    # are transfered (proxy) to MyNotes upstream (Tornado)
    location / {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://mynotes;
    }

    # Requests wich mache "port number" regex are transfered to corresponding instance of My Notes service (tornado)
    location ~ /8081$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8081;
    }
    location ~ /8082$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8082;
    }

    # Requests which start with /event and /subscriber are transfered to Pushd service (Node.js)
    location ^~ /event {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8000;
    }

    location ^~ /subscriber {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8000;
    }

}

server {
    server_name sg.mynotesapp.com;


    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/sg.mynotesapp.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/sg.mynotesapp.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

    access_log /home/inexika/mynotes/log/nginx/access_ssl.log ;
    error_log /home/inexika/mynotes/log/nginx/error_ssl.log notice;

    client_max_body_size 32m;
    proxy_buffering off;
    proxy_read_timeout 300s;

    # Tengine feature - http://tengine.taobao.org/document/http_core.html
    proxy_request_buffering off;

    # Root of the server is permanently redirected to myworkplacfe.mobi (301 redirect)
    location = / {
       rewrite ^ http://www.mynotesapp.com/ permanent;
    }

    location ~ /stats {
        root /home/inexika/mynotes/www/;
        index index.html;
    }


    # All other requests except those.
    # started with /event and /subscriber and mache "port number" regex
    # are transfered (proxy) to MyNotes upstream (Tornado)
    location / {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://mynotes;
    }
                                                    
    # Requests wich mache "port number" regex are transfered to corresponding instance of My Notes service (tornado)
    location ~ /8081$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8081;
    }
    location ~ /8082$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8082;
    }
    # Requests which start with /event and /subscriber are transfered to Pushd service (Node.js)
    location ^~ /event {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8000;
    }

    location ^~ /subscriber {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8000;
    }
}
