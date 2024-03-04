# Use an official lightweight Python image.
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /code

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the local code to the container
COPY ./app /code/app

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
