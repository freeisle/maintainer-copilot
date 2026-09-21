{
  "samples": 25,
  "accuracy": 0.92,
  "ablate": true,
  "errors": [
    {
      "query": "数据库连接超时的报错怎么解决",
      "expected": "none",
      "pred": "search_issues",
      "correct": false
    },
    {
      "query": "定时任务的实现代码在哪里",
      "expected": "none",
      "pred": "read_file",
      "correct": false
    }
  ],
  "confusion": {
    "get_issue->get_issue": 6,
    "search_issues->search_issues": 7,
    "read_file->read_file": 7,
    "none->none": 3,
    "none->search_issues": 1,
    "none->read_file": 1
  }
}