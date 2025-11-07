FROM ubuntu:22.04

# Install Vinagre and clean up
RUN apt-get update && \
    apt-get install -y vinagre && \
    rm -rf /var/lib/apt/lists/*

# Set the entrypoint to run Vinagre
ENTRYPOINT ["vinagre"]