# M2 人工 PDF 样例

本目录仅含人工测试文字，不含外部业务资料。`generate.py` 用当前系统 Cairo
1.16.0（包1.16.0-5ubuntu2.1）和 Noto Sans CJK SC（fonts-noto-cjk
1:20220127+repack1-1，Regular.ttc）生成；不引入应用运行依赖。
字体子集嵌入 PDF，字体声明见 [NOTO-LICENSE.txt](NOTO-LICENSE.txt)。

- `chinese.pdf`：两页可提取文字，Cairo生成字体子集和Unicode映射。物理第1页
  两行“部署说明”“数据库备份”，第2页“恢复步骤”“验证数据完整性”。
  规范化文本分别10/12个Unicode码点（包括中间换行），两页都是行1–2。
- `scanned.pdf`：第一张源绘图的420×240像素栅格，仅Image XObject，无文字层。
  不是空白页，也不是物理扫描仪采集；用于模拟纯图片扫描件，不要求OCR。
- `source-page-1.png`、`source-page-2.png`：同一绘图函数输出，已人工查看中文
  字形及内容；不是独立PDF阅读器渲染的证据，不能以它们证明浏览器PDF预览。

固定文件 SHA-256：

```text
1301ba319216e58b784b2034a6c85f5e0bd36508d31ae6c3cccbb3b397b66a68  chinese.pdf
137c42eb547c364e8d7da1db9a1d6ebcebf6e49ae5436294f201a1794fdac65d  scanned.pdf
```

重新生成应使用独立输出目录并比较，不覆盖已归档夹具：

```bash
python3 test/fixtures/m2/generate.py /tmp/your-new-fixture-directory
```

固定PDF日期不保证不同字体/Cairo版本逐字节一致。测试直接读取已提交PDF，
不在运行时生成样例；测试预期为独立字面值，不调用生成器或被测解析器生成答案。
本增量是补已有实现的真实覆盖，不声称发现并修复了生产缺陷或经历了生产RED。
