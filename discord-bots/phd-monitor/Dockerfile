FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY monitor.py .

# Create state directory
RUN mkdir -p /app/state

# Run as non-root user for security
RUN useradd -m -u 1000 monitor
RUN chown -R monitor:monitor /app
USER monitor

# Default command - run the monitoring loop
CMD ["python", "-u", "monitor.py"]