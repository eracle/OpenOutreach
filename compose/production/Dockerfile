FROM python:3.6

ARG APP_HOME=/app

COPY ./compose/production/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start

COPY ./compose/production/wait-for-selenium /wait-for-selenium
RUN sed -i 's/\r$//g' /wait-for-selenium
RUN chmod +x /wait-for-selenium

# Requirements are installed here to ensure they will be cached.
COPY ./requirements /requirements
RUN pip install -r /requirements/production.txt

# copy application code to WORKDIR
COPY . ${APP_HOME}

WORKDIR ${APP_HOME}

