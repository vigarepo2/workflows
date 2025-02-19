# Use Ubuntu 22.04 as the base image
FROM ubuntu:22.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Update and install required packages
RUN apt-get -y update && apt-get -y upgrade && \
    apt-get -y install apache2 unzip software-properties-common wget git snapd && \
    add-apt-repository -y ppa:ondrej/php && \
    apt-get -y update && apt-get -y install php7.4 php7.4-common php7.4-mysql php7.4-xml php7.4-xmlrpc \
    php7.4-curl php7.4-gd php7.4-imagick php7.4-cli php7.4-dev php7.4-imap php7.4-mbstring php7.4-opcache \
    php7.4-soap php7.4-zip php7.4-intl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Clone Rapidleech
RUN rm -rf /var/www/html && git clone https://github.com/PBhadoo/Rapidleech /var/www/html && \
    mkdir -p /var/www/html/files && chmod 777 /var/www/html/files && \
    chmod 777 /var/www/html/configs && chmod 777 /var/www/html/configs/files.lst

# Install RAR
RUN cd /var/www/html && rm -rf rar && \
    wget https://rarlab.com/rar/rarlinux-x64-612.tar.gz && tar -xvf rarlinux-x64-612.tar.gz && rm -f rarlinux-x64-612.tar.gz && \
    chmod -R 777 rar && chmod -R 777 rar/*

# Enable Apache modules and restart service
RUN a2enmod rewrite && service apache2 restart

# Expose port 80
EXPOSE 80

# Start Apache in foreground
CMD ["apachectl", "-D", "FOREGROUND"]
