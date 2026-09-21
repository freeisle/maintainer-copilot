# 候补购票领域术语与代码映射

## 业务口径(依据 ai-service 知识文档 knowledge/候补购票.md)

- 候补购票: 所需车次、席别无票时, 旅客提交候补订单并预付票款, 系统在有退票或余票放出时按候补顺序自动兑现车票
- 兑现: 候补订单成功出票的过程; 兑现失败或超时未兑现, 预付款全额退还, 不收取手续费
- 提高成功率: 多选相邻日期/多车次/多席别扩大范围; 越早提交排队越靠前

## 代码映射(检索相关实现时优先定位)

- 席别候补标识: ticket-service `dto/domain/SeatClassDTO` 的 `candidate` 字段(Boolean, "席别候补标识")
- 候补资格判定: ticket-service `TicketServiceImpl` —— 席别售罄(余票 quantity<=0)即开放候补, 供上层 AI 候补推荐感知
- 业务知识文档: ai-service `knowledge/候补购票.md`

## 中英术语对照

- 候补购票 = waitlist; 候补 = candidate / waitlist
- 兑现 = fulfill / issue ticket
- 席别 = seat class
