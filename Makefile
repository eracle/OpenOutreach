dev:
	docker run -p 4444:4444 -p 5900:5900 --publish-all --shm-size="128M" selenium/standalone-chrome-debug

view:
	vinagre localhost:5900

companies:
	scrapy crawl companies -a selenium_hostname=localhost -o users.csv

tests:
	pytest linkedin/tests/*
