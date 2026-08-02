#!/system/bin/sh
# 内存监控脚本，每30秒记录一次
cd /data/local/tmp/dumpmeminfo

# 记录启动时间
echo "=== 监控启动于 $(date) ===" >> dump.log

i=0
while [ $i -le 69120 ]; do
  let i++
  # 记录时间戳
  date "+%Y-%m-%d %H:%M:%S" >> output.txt
  # 执行内存转储，并将错误输出追加到 dump.log
  dumpsys meminfo --package com.baidu.naviauto >> output.txt 2>> dump.log
  sleep 30
done