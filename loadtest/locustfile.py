from locust import HttpUser, task, between


class NoticeUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def get_notice(self):
        self.client.get("/notice")
