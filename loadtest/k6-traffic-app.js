import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 50 },
    { duration: '1m', target: 200 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
  },
};

const baseUrl = __ENV.BASE_URL || 'http://localhost:8000/api';

export default function () {
  const response = http.get(`${baseUrl}/notice`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'notice returned': (r) => r.json('notice') !== undefined,
    'counter recorded': (r) => r.json('counter.total_hits') !== undefined,
  });

  sleep(1);
}
