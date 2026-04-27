




## 迭代方向

- 优化UI
- 优化内容生成文案
- 加一个直接使用原声音选项
- 加一个 直译 选项，可以支持 英文先直译成中文（不做内容理解），再直接文转音
- 当未填标题时，自动获取或生成标题（根据script.md的内容）
- 优化TTS效果
- default音色变成 “云希”
- 去除“导入外部RSS订阅”


- 最后的产品形态是一个多模态个人知识库：从各种渠道得到的信息源，转化成中文文章和中文音频.
- 把整个产品名称改成 “第二大脑计划”


Phase 1a: SQLite 数据模型 + Library 主页（文章卡片列表）
Phase 1b: 文章详情页 （源格式+预览+编辑保存功能）
Phase 1c: 搜索 + 标签
Phase 2a: RSS 订阅自动拉取
Phase 2b: 播客音频转录
Phase 2c: Bookmarklet
Phase 3:  看 Phase 1-2 用得爽不爽再决定

Tag articles inline during LLM generation (distill, format, translate)
Background retag thread on startup for existing untagged scripts
YouTube "use original audio" option: downloads mp3, uses format_transcript
Add literal_translate and literal_translate_chunk for no-loss translation
Auto-generate title via LLM when title field left blank
Auto-publish to GitHub Pages after generation with "publishing" status
Default TTS voice changed to zh-CN-YunxiNeural
Fix YouTube audio download: use bestaudio/best + FFmpegExtractAudio
Fix thumbnail download: switch to urllib.request (SSL/timeout fix)
Add db functions: get_untagged_articles, update_tags, list_all_tags



方案二：独立 RSS 管理页 /episodes

专门的播客集数列表页，类似播客后台：

表格展示：标题、时长、发布时间、音频大小
每行操作：删除、编辑标题/描述、重新发布
拖拽排序（影响 RSS 中的顺序）
批量删除

方案二步骤：

后端 (~1h)

GET /api/episodes — 读 episodes.json，返回数组
DELETE /api/episodes/{idx} — 删条目，重生成 RSS，可选删音频文件
PATCH /api/episodes/{idx} — 改 title/description，重生成 RSS
POST /api/episodes/reorder — 接收新顺序，重写 episodes.json，重生成 RSS
前端 (~1.5h)
5. templates/episodes.html — 表格展示，每行有编辑/删除按钮
6. 拖拽排序用 SortableJS（CDN 引入，5 行代码）

总计：约 2.5h，不复杂。

数据层最简单，episodes.json 是 flat list。主要工作是前端表格。要做吗？



知识库动作更新
- 知识库左边栏不要是source的分类，而是文章内容的分类，source可以像现在这样，标记在每篇文章的卡片上
- 如果要做一个像obsidian那样的知识图谱，

