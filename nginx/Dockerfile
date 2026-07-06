# nginx/Dockerfile
FROM nginx:alpine

# Copy configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy static frontend files
COPY html/ /usr/share/nginx/html/

# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/example_ssl.conf

# Expose port 80
EXPOSE 80

# Run nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
