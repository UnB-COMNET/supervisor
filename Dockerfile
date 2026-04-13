# Python Slim Image
FROM python:3.13.0-bullseye

# Work directory
WORKDIR /supervisor

# Copying all necessary files
COPY app/ /supervisor/app/
COPY setup.py /supervisor/setup.py
COPY requirements.txt /supervisor/requirements.txt

# Install Docker CLI
RUN apt-get update && apt-get install -y docker.io && rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 5151

CMD ["python", "-m", "flask", "--app", "app.routes", "run", "--port", "5151", "--host", "0.0.0.0"]

# Build with a name
# sudo docker build -t supervisor .
# Run with host network mode to access mapped onos instance and deployer from the virtual machine
# sudo docker run --rm -it --network host --name supervisor supervisor
