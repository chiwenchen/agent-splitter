# API Key 認證設計

## 概述

目前 endpoint 是完全公開的，任何人都可以呼叫。加上 API Key 認證後，呼叫時需在 header 帶上 `x-api-key`，否則回傳 403。

## 方案選擇

HTTP API（目前使用）不支援原生 API Key，需改用 **REST API**，SAM 原生支援 `ApiKeyRequired` 與 `UsagePlan`。

REST API 相對 HTTP API 的差異：
- 費用稍高（$3.5/百萬 vs $1/百萬），但用量低無感
- 原生支援 API Key、Usage Plan、Throttling

## SAM template 修改

```yaml
# template.yaml

Resources:
  SplitSettleApi:
    Type: AWS::Serverless::Api
    Properties:
      StageName: prod
      Auth:
        ApiKeyRequired: true          # 所有 endpoint 預設需要 API Key

  SplitSettleFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/split_settle/
      Handler: handler.lambda_handler
      Runtime: python3.13
      Events:
        SplitSettle:
          Type: Api
          Properties:
            RestApiId: !Ref SplitSettleApi
            Path: /split_settle
            Method: post

  ApiKey:
    Type: AWS::ApiGateway::ApiKey
    DependsOn: SplitSettleApiProdStage
    Properties:
      Name: split-settle-key
      Enabled: true

  UsagePlan:
    Type: AWS::ApiGateway::UsagePlan
    DependsOn: SplitSettleApiProdStage
    Properties:
      UsagePlanName: split-settle-plan
      ApiStages:
        - ApiId: !Ref SplitSettleApi
          Stage: prod
      Throttle:
        RateLimit: 10       # 每秒最多 10 次
        BurstLimit: 20

  UsagePlanKey:
    Type: AWS::ApiGateway::UsagePlanKey
    Properties:
      KeyId: !Ref ApiKey
      KeyType: API_KEY
      UsagePlanId: !Ref UsagePlan

Outputs:
  ApiUrl:
    Value: !Sub "https://${SplitSettleApi}.execute-api.${AWS::Region}.amazonaws.com/prod/split_settle"
  ApiKeyId:
    Value: !Ref ApiKey
    Description: "Run: aws apigateway get-api-key --api-key <id> --include-value to get the key value"
```

## 取得 API Key 值

部署後執行：

```bash
aws apigateway get-api-key \
  --api-key <ApiKeyId from output> \
  --include-value \
  --region ap-northeast-1 \
  --query 'value' --output text
```

## 呼叫方式

```bash
curl -X POST https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/split_settle \
  -H "Content-Type: application/json" \
  -H "x-api-key: <your-api-key>" \
  -d '{ ... }'
```

## MCP Server 整合

`mcp_server/server.py` 改從環境變數讀取 API Key：

```python
import os

API_KEY = os.environ.get("SPLIT_SETTLE_API_KEY", "")

# 在 httpx request 加上 header
response = await client.post(
    API_URL,
    json=arguments,
    headers={"x-api-key": API_KEY},
    timeout=10
)
```

Claude Desktop config 加上環境變數：

```json
{
  "mcpServers": {
    "split-settle": {
      "command": "python3",
      "args": ["/path/to/agent-splitter/mcp_server/server.py"],
      "env": {
        "SPLIT_SETTLE_API_KEY": "your-api-key-here"
      }
    }
  }
}
```
