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
    && rm -rf /var/lib/apt/lists/*

# Configure Tor
RUN echo "ControlPort 9051" >> /etc/tor/torrc && \
    echo "CookieAuthentication 0" >> /etc/tor/torrc


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
