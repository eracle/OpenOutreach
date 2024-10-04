# Use the base Python image
FROM python:3.11

# Define the application directory
ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Define a build argument to determine which environment to use
ARG BUILD_ENV=production

# Copy requirements folder and install the dependencies based on BUILD_ENV
COPY ./requirements /requirements
RUN pip install -r /requirements/${BUILD_ENV}.txt

# Copy necessary startup scripts
COPY ./compose/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

COPY ./compose/start_companies /start_companies
RUN sed -i 's/\r$//g' /start_companies && chmod +x /start_companies

COPY ./compose/wait-for-selenium /wait-for-selenium
RUN sed -i 's/\r$//g' /wait-for-selenium && chmod +x /wait-for-selenium

# Copy the entire application code to the app directory
COPY . ${APP_HOME}

# Set the working directory
WORKDIR ${APP_HOME}
