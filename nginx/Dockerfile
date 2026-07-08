# nginx/Dockerfile
FROM nginx:alpine

# Copy configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy static frontend files
COPY html/ /usr/share/nginx/html/

# 替换 JS 版本号为构建时间戳，确保浏览器每次加载最新版本
RUN sed -i 's/\(js\/[^"]*\.js\)?v=[0-9]*/\1?v='$(date +%s)'/g' /usr/share/nginx/html/*.html

# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/example_ssl.conf

# Expose port 80
EXPOSE 80

# Run nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
