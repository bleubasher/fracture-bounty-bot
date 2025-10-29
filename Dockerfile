# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy files into container
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Run the bot
CMD ["python", "bounty_local.py"]
