# 基础统计脚本

已确定纳入范围、题型、选项和分母后，可用随包 `scripts/basic_statistics.py` 计算单选、多选、量表和指定交叉组的描述结果。结果写入后台 JSONL，供制表和复核使用，不加入交付Excel作为索引sheet。复杂抽样、配对检验、排序、特殊题组及定制模型仍按相应方法使用项目脚本；本脚本不替代这些能力。

CSV路径仅用Python标准库；SAV输入另需 `pyreadstat` 与 `pandas`（`python -m pip install pyreadstat pandas`）。SAV保留原始编码及用户定义缺失码并读取值标签，优先使用配置标签；配置没有选项文字时可使用SAV值标签。每题由明确选项及`missing`设置决定分布范围，例如“不知道”在本题作为实际选项时保留，不因SAV元数据标为用户缺失就提前删除；明确列入本题`missing`后才排除。原始数据和配置不回写。

```text
python scripts/basic_statistics.py 样本.csv 配置.json 后台结果.jsonl
```

默认不覆盖已有结果；另取版本名，或明确加 `--force`。从其他目录运行时使用脚本实际路径。命令返回数据和配置的路径、SHA256及结果条数，并分别记录用户纳入人数`user_included_rows`（兼容字段`included_rows`同义）、实际进入计算范围的`calculation_rows`及按明确规则不计入计算的零权重记录数`zero_weight_excluded_from_calculation`，保存到本项目运行记录。

## 最小配置

每题必须明确 `eligible` 显示人群，确认全员回答时写 `{"all": true}`。用户纳入范围由 `inclusion.confirmed` 和 `inclusion.filter` 记录；脚本不自行读取“质量判断”过滤数据，不自动去重，也不从复制字段推导权重。

```json
{
  "sample_version": "survey-v1",
  "inclusion": {
    "confirmed": true,
    "filter": {"field": "include", "op": "eq", "value": 1}
  },
  "groups": [
    {"id": "home_charger", "label": "有家庭充电设施",
     "filter": {"field": "home_charger", "op": "eq", "value": 1}}
  ],
  "questions": [
    {
      "id": "Q1", "label": "近期是否使用公共充电", "type": "single",
      "field": "Q1", "eligible": {"all": true}, "missing": [99],
      "options": [{"value": 1, "label": "是"}, {"value": 2, "label": "否"}]
    }
  ]
}
```

确认全部归档行均纳入时，将 `inclusion.filter` 设为 `{"all": true}`。没有该明确设置时不默认纳入全部。`sample_version`为必填；CSV可加 `encoding`（默认`utf-8-sig`）和 `delimiter`（默认逗号）。标识符类字符串按原字符串比较；数值编码在配置中写数字，可匹配CSV数值文本与SAV数值。

## 题型和分母

- **single**：`field`与按确认顺序列全的`options:[{value,label}]`。零频选项保留。空值或`missing`中的编码不进有效分母，其他未定义编码单列为无效。`missing`优先于选项表；某编码同时列入两处时，该编码的回答被排除，选项行作为零频保留。
- **multiple**：每项用`{field,label,selected_value,unselected_value}`，分别明确选中与未选编码；题目必须设置`all_zero_valid:true/false`。全空始终记未答，部分缺失记不完整，均不补为未选。此基础路径用完整、合法的多选字段确定题级有效人数；动态曝光或允许部分作答的题使用明确的项目方法。多项选择率总和可超过100%。
- **scale**：沿用single结构，另设`scores:{"1":5,"2":4,"3":3,"4":2,"5":1}`，按语义映射实际分值。每个选项须明确对应数值；`null`表示该选项保留在分布中，但排除出均值及TOP2分母。可加`top_values:[1,2]`，表示语义最高两档；该两档必须有数值分值。

全部题型输出选项比例、总计和样本量；scale另出均值，配置`top_values`时再出TOP2。`label`为题目或选项显示文字；CSV选项必须提供文字，SAV选项可由已有值标签补齐。选项展示顺序沿用配置，不自行重排。

可选权重设置：

```json
"weight": {"field": "weight", "version": "weight-v1", "zero_in_n": true}
```

比例和均值以同一有效人群的权重和作分母，实际人数另存。`zero_in_n:true`时零权重记录计入实际人数，对加权分子分母贡献为0；为false时按该明确规则不计入计算人数，用户纳入记录数仍保持，不把权重处理当作改变用户纳入决定。已确认纳入的记录出现缺失、非有限或负权重时，脚本报出原行和字段，先处理对应问题，不补成1。没有权重配置时正常计算未加权结果。分母为0返回`null`，有有效分母但未选某项返回真实0。

## 筛选与分组

题目`eligible`、用户纳入`inclusion.filter`及每个交叉组`filter`均采用同一格式：

- `{"all": true}`：明确全部；
- `{"all": [规则1,规则2]}`：同时满足；`{"any": [规则1,规则2]}`：满足任一；列表不能为空；
- 叶子规则：`{field,op,value}`或`{field,op,values}`；`op`可为`eq/ne/in/not_in/gt/ge/lt/le/missing/not_missing`。`in/not_in`使用非空`values`列表，其余数值比较使用`value`。

缺失值不会因`ne`或`not_in`自动进入某组；如需单列未知组，使用`missing`规则，必要时在该规则加`missing:[99]`。规则可组合，重叠分组允许同一记录进入多组。脚本自动提供`total`总体组，该id不用于自建分组。字段不存在、漏填显示人群或无法解释的配置会报具体位置。

## 后台结果字段

每行一个指标：`result_id`、`question_id`、`question_label`、`metric`、`option`、`group_id`、`group_label`、`numerator`、`effective_base`、`actual_n`、`weight_sum`、`weight_version`、`value`、`sample_version`、`eligible_n`、`missing_n`、`excluded_by_reason`。

`metric`分别为`proportion/total/sample_size/mean/top2`。比例保存0—1数值，制表再设百分比格式；多选总计可大于1。`sample_size`行的值是实际人数（分子为人数、分母为1）；其他比例及均值按其实际有效分母记录。均值/TOP2排除了不计分档时，行内实际人数与分母也随之改变。`weight_sum`未加权时为null，`weight_version`为`unweighted`。排除原因区分未答、不完整、无效编码及全0不构成有效回答，不能把这些记录当作真实0。

将实际使用的配置与结果同项目脚本一起留在过程文件。首次用于一种新编码或分母规则时，先用可手算的小例子核对，再计算全量数据。
