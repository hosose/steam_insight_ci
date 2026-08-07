FROM nginx:alpine AS runner

COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/assets

EXPOSE 3000

# Custom nginx config to listen on port 3000
RUN sed -i 's/listen  *80;/listen 3000;/g' /etc/nginx/conf.d/default.conf

CMD ["nginx", "-g", "daemon off;"]

FROM node:20-alpine AS dev
WORKDIR /app
COPY . .
RUN npm install -g serve
EXPOSE 3000
CMD ["serve", "-p", "3000", "."]
