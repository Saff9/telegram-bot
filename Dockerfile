FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libmagic1 \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    nodejs \
    tor \
    curl \
    aria2 \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Configure Tor
RUN echo "ControlPort 9051" >> /etc/tor/torrc && \
    echo "CookieAuthentication 0" >> /etc/tor/torrc

# Install Deno for yt-dlp signature solving
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"


# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create downloads directory
RUN mkdir -p downloads

# Run the bot
CMD ["python", "bot.py"]
