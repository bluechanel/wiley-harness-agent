# Bash 工具

让 Claude 请求 shell 命令，由您的应用程序在持久的 bash 会话中运行，并作为工具结果返回。

---

<Note>
  关于"zero data retention"（零数据保留），即 ZDR 如何适用于此功能，请参阅 [API 与数据保留](/docs/zh-CN/manage-claude/api-and-data-retention)。
</Note>

Bash 工具是一个[客户端工具](/docs/zh-CN/agents-and-tools/tool-use/how-tool-use-works)：Claude 本身不运行命令。当您在请求中包含该工具时，Claude 会回复一个 `tool_use` 块，其中指明要运行的命令。您的应用程序在其拥有的 bash 会话中运行该命令，并在 `tool_result` 块中返回输出。

您的应用程序在多次工具调用之间保持一个 bash 进程存活，因此状态会在命令之间持久保留。工作目录、环境变量以及命令创建的任何文件在下一个命令执行时仍然存在。

该工具的当前版本是 `bash_20250124`。有关模型支持、beta 标头和早期版本，请参阅[工具版本](#tool-versions)。有关 Anthropic 提供的所有工具，请参阅[工具参考](/docs/zh-CN/agents-and-tools/tool-use/tool-reference)。

## 使用场景

- **开发工作流：** 运行构建命令、测试和开发工具
- **系统自动化：** 执行脚本、管理文件、自动化任务
- **数据处理：** 处理文件、运行分析脚本、管理数据集
- **环境设置：** 安装软件包、配置环境

## 快速开始

<CodeGroup>
  ```bash cURL
  curl https://api.anthropic.com/v1/messages \
    -H "content-type: application/json" \
    -H "x-api-key: $ANTHROPIC_API_KEY" \
    -H "anthropic-version: 2023-06-01" \
    -d '{
      "model": "claude-opus-4-8",
      "max_tokens": 1024,
      "tools": [
        {
          "type": "bash_20250124",
          "name": "bash"
        }
      ],
      "messages": [
        {
          "role": "user",
          "content": "List all Python files in the current directory."
        }
      ]
    }'
  ```

```bash CLI
ant messages create \
  --model claude-opus-4-8 \
  --max-tokens 1024 \
  --tool '{type: bash_20250124, name: bash}' \
  --message '{role: user, content: List all Python files in the current directory.}'
```

```python Python
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=1024,
    tools=[{"type": "bash_20250124", "name": "bash"}],
    messages=[
        {"role": "user", "content": "List all Python files in the current directory."}
    ],
)

print(response)
```

```typescript TypeScript
const client = new Anthropic();

const response = await client.messages.create({
  model: "claude-opus-4-8",
  max_tokens: 1024,
  tools: [{ type: "bash_20250124", name: "bash" }],
  messages: [
    {
      role: "user",
      content: "List all Python files in the current directory.",
    },
  ],
});

console.log(response);
```

```csharp C#
var client = new AnthropicClient();

var response = await client.Messages.Create(
    new()
    {
        Model = Model.ClaudeOpus4_8,
        MaxTokens = 1024,
        Tools = [new ToolBash20250124()],
        Messages =
        [
            new()
            {
                Role = Role.User,
                Content = "List all Python files in the current directory.",
            },
        ],
    }
);

Console.WriteLine(response);
```

```go Go
client := anthropic.NewClient()

response, err := client.Messages.New(context.TODO(), anthropic.MessageNewParams{
	Model:     anthropic.ModelClaudeOpus4_8,
	MaxTokens: 1024,
	Tools: []anthropic.ToolUnionParam{
		{OfBashTool20250124: &anthropic.ToolBash20250124Param{}},
	},
	Messages: []anthropic.MessageParam{
		anthropic.NewUserMessage(anthropic.NewTextBlock("List all Python files in the current directory.")),
	},
})
if err != nil {
	log.Fatal(err)
}
fmt.Println(response)
```

```java Java
import com.anthropic.models.messages.ToolBash20250124;

void main() {
    AnthropicClient client = AnthropicOkHttpClient.fromEnv();

    Message response = client.messages().create(
        MessageCreateParams.builder()
            .model(Model.CLAUDE_OPUS_4_8)
            .maxTokens(1024)
            .addTool(ToolBash20250124.builder().build())
            .addUserMessage("List all Python files in the current directory.")
            .build()
    );

    IO.println(response);
}
```

```php PHP
use Anthropic\Messages\ToolBash20250124;

$client = new Client();

$response = $client->messages->create(
    model: 'claude-opus-4-8',
    maxTokens: 1024,
    tools: [new ToolBash20250124()],
    messages: [
        ['role' => 'user', 'content' => 'List all Python files in the current directory.'],
    ],
);

echo $response;
```

```ruby Ruby
client = Anthropic::Client.new

response = client.messages.create(
  model: "claude-opus-4-8",
  max_tokens: 1024,
  tools: [{type: "bash_20250124", name: "bash"}],
  messages: [
    {role: "user", content: "List all Python files in the current directory."}
  ]
)

puts response
```

</CodeGroup>

Claude 会以 `stop_reason: "tool_use"` 响应，并返回一个 `tool_use` 块，其中包含供您的应用程序运行的命令：

```json Output
{
  "id": "msg_01XAbCDeFgHiJkLmNoPQrStU",
  "model": "claude-opus-4-8",
  "stop_reason": "tool_use",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "I'll list all Python files in the current directory for you."
    },
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917835lq9",
      "name": "bash",
      "input": {
        "command": "ls *.py"
      }
    }
  ]
}
```

在您的 bash 会话中运行 `input.command`，并将输出作为 `tool_result` 发送回去。有关完整的往返流程，请参阅[实现 bash 工具](#implement-the-bash-tool)。

## 工作原理

每次工具调用都是 Claude 与您的应用程序之间的一次往返：

1. Claude 返回一个包含要运行的 `command` 的 `tool_use` 块。
2. 您的应用程序在其 bash 会话中运行该命令。
3. 您的应用程序将命令的输出（stdout 和 stderr 合并在一起）通过 `tool_result` 块返回给 Claude。
4. Claude 要么在同一会话中请求另一个命令，要么以文本形式响应。

Claude 也可以在一次响应中返回多个 `tool_use` 块。请在同一会话中按顺序运行它们，并在一条 `user` 消息中返回所有结果。请参阅[并行工具使用](/docs/zh-CN/agents-and-tools/tool-use/parallel-tool-use)。

API 是无状态的。您的 shell 会话的任何信息都不会在请求之间传递，因此由您的应用程序决定会话何时开始、存活多久以及何时重启。有关完整的请求和响应周期，请参阅[处理工具调用](/docs/zh-CN/agents-and-tools/tool-use/handle-tool-calls)。

## 参数

Bash 工具定义有两个必需字段：`type` 和 `name`，且 `name` 必须为 `bash`。该工具是无模式（schema-less）的：您无需提供 `input_schema`，因为模式已内置于 Claude 的模型中且无法修改。下表列出了 Claude 调用该工具时设置的输入字段。

| 参数      | 必需 | 描述                           |
| --------- | ---- | ------------------------------ |
| `command` | 是\* | 要运行的 bash 命令             |
| `restart` | 否   | 设置为 `true` 以重启 bash 会话 |

\*除非使用 `restart`，否则为必需

要处理 `restart: true`，请终止 shell 进程，启动一个新进程，并返回一个确认重启的 `tool_result`。重启后的会话是全新的：工作目录、环境变量以及任何正在运行的进程都会消失。

<Accordion title="使用示例">
  运行命令：

```json
{
  "command": "ls -la *.py"
}
```

重启会话：

```json
{
  "restart": true
}
```

</Accordion>

## 工具版本

`bash_20250124` 是该工具的当前版本，不需要 beta 标头。从 Claude Sonnet 3.7（[已停用](/docs/zh-CN/about-claude/model-deprecations)）开始的每个模型都接受它，包括所有当前的 Claude 模型。

最初的 `bash_20241022` 版本是计算机使用 beta 的一部分，2024 年 10 月发布的 Claude Sonnet 3.5（[已停用](/docs/zh-CN/about-claude/model-deprecations)）是唯一接受它的模型。使用它的请求需要 `anthropic-beta: computer-use-2024-10-22` 标头，并且 SDK 仅在其 beta 命名空间中公开它。新的集成应使用 `bash_20250124`。

## 示例：多步骤自动化

Claude 可以跨多次工具调用串联命令以完成多步骤任务：

```text
User request:
"Install the requests library and create a simple Python script that
fetches a joke from an API, then run it."

Claude's tool uses:
1. Install package
   {"command": "pip install requests"}

2. Create script
   {"command": "cat > fetch_joke.py << 'EOF'\nimport requests\nresponse = requests.get('https://official-joke-api.appspot.com/random_joke')\njoke = response.json()\nprint(f\"Setup: {joke['setup']}\")\nprint(f\"Punchline: {joke['punchline']}\")\nEOF"}

3. Run script
   {"command": "python fetch_joke.py"}
```

会话在命令之间保持状态，因此在步骤 2 中创建的文件在步骤 3 中仍然可用。

## 实现 bash 工具

Claude 决定运行哪个命令。您的应用程序负责其他一切：shell 进程、超时和安全检查。以下步骤展示了一个最小化实现。

<Steps>
  <Step title="创建持久的 bash 会话">
    启动一个长期存活的 bash 进程，并在其中运行每个命令。由于指向存活进程的管道永远不会报告文件结束（end-of-file），会话会在每个命令之后打印一个唯一的哨兵行，以标记该命令输出的结束位置：

    <CodeGroup exclude="shell">
      ```python Python
      import subprocess
      import uuid


      class BashSession:
          """A bash process that stays alive between commands so state persists."""

          def __init__(self):
              self.process = subprocess.Popen(
                  ["/bin/bash"],
                  stdin=subprocess.PIPE,
                  stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT,  # interleave errors with output, in order
                  start_new_session=True,  # own process group: a timeout can kill every child
                  text=True,
              )

          def execute_command(self, command):
              """Run a command in the session and return its output."""
              sentinel = f"__CLAUDE_BASH_DONE_{uuid.uuid4().hex}__"  # unique per call
              self.process.stdin.write(f"{command}\necho {sentinel}\n")
              self.process.stdin.flush()

              output = []
              for line in self.process.stdout:
                  if sentinel in line:  # this command's output is complete
                      break
                  output.append(line)
              return "".join(output)

          def restart(self):
              self.process.kill()
              self.process.wait()
              self.__init__()


      bash_session = BashSession()
      print(bash_session.execute_command("cd /tmp && pwd"))
      print(bash_session.execute_command("pwd"))  # still /tmp: the session kept its state
      ```

      ```typescript TypeScript
      import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
      import { createInterface, type Interface } from "node:readline";
      import { randomUUID } from "node:crypto";

      // 一个在命令之间保持存活的 bash 进程，以便状态得以持久保留。
      class BashSession {
        process!: ChildProcessWithoutNullStreams;
        private lines!: Interface;

        constructor() {
          this.start();
        }

        private start(): void {
          this.process = spawn("/bin/bash", {
            detached: true // own process group: a timeout can kill every child
          });
          this.process.stdin.write("exec 2>&1\n"); // interleave errors with output, in order
          this.lines = createInterface({ input: this.process.stdout });
        }

        // 在会话中运行命令并返回其输出。
        executeCommand(command: string): Promise<string> {
          const sentinel = `__CLAUDE_BASH_DONE_${randomUUID()}__`; // unique per call
          const output: string[] = [];
          const result = new Promise<string>((resolve) => {
            const onLine = (line: string): void => {
              if (line.includes(sentinel)) {
                // 此命令的输出已完成
                this.lines.off("line", onLine);
                resolve(output.join(""));
              } else {
                output.push(`${line}\n`);
              }
            };
            this.lines.on("line", onLine);
          });
          this.process.stdin.write(`${command}\necho ${sentinel}\n`);
          return result;
        }

        restart(): void {
          this.process.kill("SIGKILL");
          this.lines.close();
          this.start();
        }
      }

      const session = new BashSession();
      console.log(await session.executeCommand("cd /tmp && pwd"));
      console.log(await session.executeCommand("pwd")); // still /tmp: the session kept its state
      session.process.stdin.end(); // closing stdin ends the shell so the script can exit
      ```

      ```csharp C#
      using System.Diagnostics;
      using System.Text;

      var session = new BashSession();
      Console.Write(session.ExecuteCommand("cd /tmp && pwd"));
      Console.Write(session.ExecuteCommand("pwd")); // still /tmp: the session kept its state

      // 一个在命令之间保持存活的 bash 进程，以便状态得以持久保留。
      class BashSession
      {
          public Process Process { get; private set; }

          public BashSession()
          {
              Process = Start();
          }

          static Process Start()
          {
              var process = Process.Start(new ProcessStartInfo("/bin/bash")
              {
                  RedirectStandardInput = true,
                  RedirectStandardOutput = true
              })!;
              process.StandardInput.Write("exec 2>&1\n"); // interleave errors with output, in order
              process.StandardInput.Flush();
              return process;
          }

          // 在会话中运行一条命令并返回其输出。
          public string ExecuteCommand(string command)
          {
              var sentinel = $"__CLAUDE_BASH_DONE_{Guid.NewGuid():N}__"; // unique per call
              Process.StandardInput.Write($"{command}\necho {sentinel}\n");
              Process.StandardInput.Flush();

              var output = new StringBuilder();
              while (Process.StandardOutput.ReadLine() is string line)
              {
                  if (line.Contains(sentinel)) // this command's output is complete
                  {
                      break;
                  }
                  output.Append(line).Append('\n');
              }
              return output.ToString();
          }

          public void Restart()
          {
              Process.Kill(entireProcessTree: true);
              Process.WaitForExit();
              Process = Start();
          }
      }
      ```

      ```go Go
      import (
      	"bufio"
      	"crypto/rand"
      	"encoding/hex"
      	"fmt"
      	"io"
      	"log"
      	"os/exec"
      	"strings"
      	"syscall"
      )

      // BashSession 是一个在命令之间保持存活的 bash 进程，因此状态得以持久保留。
      type BashSession struct {
      	cmd    *exec.Cmd
      	stdin  io.WriteCloser
      	output *bufio.Reader
      }

      func NewBashSession() (*BashSession, error) {
      	cmd := exec.Command("/bin/bash")
      	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // own process group: a timeout can kill every child
      	stdin, err := cmd.StdinPipe()
      	if err != nil {
      		return nil, err
      	}
      	stdout, err := cmd.StdoutPipe()
      	if err != nil {
      		return nil, err
      	}
      	cmd.Stderr = cmd.Stdout // interleave errors with output, in order
      	if err := cmd.Start(); err != nil {
      		return nil, err
      	}
      	return &BashSession{cmd: cmd, stdin: stdin, output: bufio.NewReader(stdout)}, nil
      }

      // ExecuteCommand 在会话中运行一条命令并返回其输出。
      func (s *BashSession) ExecuteCommand(command string) string {
      	buf := make([]byte, 16)
      	rand.Read(buf)
      	sentinel := fmt.Sprintf("__CLAUDE_BASH_DONE_%s__", hex.EncodeToString(buf)) // unique per call
      	fmt.Fprintf(s.stdin, "%s\necho %s\n", command, sentinel)

      	var output strings.Builder
      	for {
      		line, err := s.output.ReadString('\n')
      		if err != nil || strings.Contains(line, sentinel) { // this command's output is complete
      			break
      		}
      		output.WriteString(line)
      	}
      	return output.String()
      }

      // Restart 会终止该 shell 并在原处启动一个全新的会话。
      func (s *BashSession) Restart() error {
      	s.cmd.Process.Kill()
      	s.cmd.Wait()
      	fresh, err := NewBashSession()
      	if err != nil {
      		return err
      	}
      	*s = *fresh
      	return nil
      }

      func main() {
      	session, err := NewBashSession()
      	if err != nil {
      		log.Fatal(err)
      	}
      	fmt.Print(session.ExecuteCommand("cd /tmp && pwd"))
      	fmt.Print(session.ExecuteCommand("pwd")) // still /tmp: the session kept its state
      }
      ```

      ```java Java
      import java.io.BufferedReader;
      import java.io.BufferedWriter;
      import java.io.IOException;
      import java.io.InputStreamReader;
      import java.io.OutputStreamWriter;
      import java.util.UUID;

      // 一个在命令之间保持存活的 bash 进程，以便状态得以持久保留。
      class BashSession {
          Process process;
          BufferedWriter stdin;
          BufferedReader output;

          BashSession() throws IOException {
              start();
          }

          void start() throws IOException {
              ProcessBuilder builder = new ProcessBuilder("/bin/bash");
              builder.redirectErrorStream(true); // interleave errors with output, in order
              process = builder.start();
              stdin = new BufferedWriter(new OutputStreamWriter(process.getOutputStream()));
              output = new BufferedReader(new InputStreamReader(process.getInputStream()));
          }

          // 在会话中运行命令并返回其输出。
          String executeCommand(String command) throws IOException {
              String sentinel = "__CLAUDE_BASH_DONE_" + UUID.randomUUID() + "__"; // unique per call
              stdin.write(command + "\necho " + sentinel + "\n");
              stdin.flush();

              StringBuilder result = new StringBuilder();
              String line;
              while ((line = output.readLine()) != null) {
                  if (line.contains(sentinel)) { // this command's output is complete
                      break;
                  }
                  result.append(line).append("\n");
              }
              return result.toString();
          }

          void restart() throws IOException, InterruptedException {
              process.destroyForcibly();
              process.waitFor();
              start();
          }
      }

      void main() throws Exception {
          BashSession session = new BashSession();
          IO.println(session.executeCommand("cd /tmp && pwd"));
          IO.println(session.executeCommand("pwd")); // still /tmp: the session kept its state
      }
      ```

      ```php PHP
      // 一个在命令之间保持存活的 bash 进程，以便状态得以持久保留。
      class BashSession
      {
          public $process;
          public $stdin;
          public $output;

          public function __construct()
          {
              $this->start();
          }

          private function start(): void
          {
              // setsid 为 shell 提供独立的进程组：超时时可以终止所有子进程
              $this->process = proc_open(
                  ['setsid', '/bin/bash'],
                  [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['redirect', 1]], // interleave errors with output
                  $pipes
              );
              $this->stdin = $pipes[0];
              $this->output = $pipes[1];
          }

          // 在会话中运行命令并返回其输出。
          public function executeCommand(string $command): string
          {
              $sentinel = '__CLAUDE_BASH_DONE_' . bin2hex(random_bytes(16)) . '__'; // unique per call
              fwrite($this->stdin, "{$command}\necho {$sentinel}\n");
              fflush($this->stdin);

              $output = '';
              while (($line = fgets($this->output)) !== false) {
                  if (str_contains($line, $sentinel)) { // this command's output is complete
                      break;
                  }
                  $output .= $line;
              }
              return $output;
          }

          public function restart(): void
          {
              proc_terminate($this->process, 9);
              proc_close($this->process);
              $this->start();
          }
      }

      $session = new BashSession();
      echo $session->executeCommand("cd /tmp && pwd");
      echo $session->executeCommand("pwd"); // still /tmp: the session kept its state
      ```

      ```ruby Ruby
      require "open3"
      require "securerandom"

      # 一个在命令之间保持存活的 bash 进程，以便状态得以持久保留。
      class BashSession
        attr_reader :output, :wait_thread

        def initialize
          start
        end

        # 在会话中运行一条命令并返回其输出。
        def execute_command(command)
          sentinel = "__CLAUDE_BASH_DONE_#{SecureRandom.hex(16)}__" # unique per call
          @stdin.write("#{command}\necho #{sentinel}\n")
          @stdin.flush

          output = +""
          @output.each_line do |line|
            break if line.include?(sentinel) # this command's output is complete

            output << line
          end
          output
        end

        def restart
          Process.kill("KILL", @wait_thread.pid)
          @wait_thread.join
          start
        end

        private

        def start
          # popen2e 将错误与输出按顺序交错；pgroup 为 shell 提供其
          # 独立的进程组，以便超时时可以终止所有子进程
          @stdin, @output, @wait_thread = Open3.popen2e("/bin/bash", pgroup: true)
        end
      end

      session = BashSession.new
      puts session.execute_command("cd /tmp && pwd")
      puts session.execute_command("pwd") # still /tmp: the session kept its state
      ```
    </CodeGroup>

    会话将 stderr 与 stdout 交错输出，因此错误消息会出现在它们发生的位置。该示例省略了完整实现还需要的内容：一个超时机制，当命令挂起时终止 shell 及其启动的所有进程，然后重启会话。[使用命令超时](#follow-implementation-best-practices)最佳实践展示了一种添加方式。

  </Step>

  <Step title="处理 Claude 的工具调用">
    从 Claude 的响应中提取并运行命令：

    <CodeGroup exclude="shell">
      ```python Python
      tool_results = []
      for content in response.content:
          if content.type == "tool_use" and content.name == "bash":
              if content.input.get("restart"):
                  bash_session.restart()
                  result = "Bash session restarted"
              else:
                  command = content.input.get("command")
                  result = bash_session.execute_command(command)

              # 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
              tool_results.append(
                  {"type": "tool_result", "tool_use_id": content.id, "content": result}
              )
      ```

      ```typescript TypeScript
      const toolResults: { type: string; tool_use_id: string; content: string }[] = [];
      for (const block of response.content) {
        if (block.type === "tool_use" && block.name === "bash") {
          let result: string;
          if (block.input.restart) {
            bashSession.restart();
            result = "Bash session restarted";
          } else {
            result = await bashSession.executeCommand(block.input.command ?? "");
          }

          // 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
          toolResults.push({ type: "tool_result", tool_use_id: block.id, content: result });
        }
      }
      ```

      ```csharp C#
      var toolResults = new List<ToolResultBlockParam>();
      foreach (var block in response.Content)
      {
          if (block.TryPickToolUse(out var toolUse) && toolUse.Name == "bash")
          {
              string result;
              if (toolUse.Input.TryGetValue("restart", out var restart) && restart.GetBoolean())
              {
                  bashSession.Restart();
                  result = "Bash session restarted";
              }
              else
              {
                  var command = toolUse.Input["command"].GetString() ?? "";
                  result = bashSession.ExecuteCommand(command);
              }

              // 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
              toolResults.Add(new ToolResultBlockParam { ToolUseID = toolUse.ID, Content = result });
          }
      }
      ```

      ```go Go
      var toolResults []anthropic.ContentBlockParamUnion
      for _, block := range response.Content {
      	if block.Type == "tool_use" && block.Name == "bash" {
      		var input struct {
      			Command string `json:"command"`
      			Restart bool   `json:"restart"`
      		}
      		if err := json.Unmarshal(block.Input, &input); err != nil {
      			log.Fatal(err)
      		}

      		var result string
      		if input.Restart {
      			bashSession.Restart()
      			result = "Bash session restarted"
      		} else {
      			result = bashSession.ExecuteCommand(input.Command)
      		}

      		// 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
      		toolResults = append(toolResults, anthropic.NewToolResultBlock(block.ID, result, false))
      	}
      }
      ```

      ```java Java
      List<Map<String, Object>> toolResults = new ArrayList<>();
      for (ContentBlock block : response.content()) {
          if (block.type().equals("tool_use") && block.name().equals("bash")) {
              String result;
              if (Boolean.TRUE.equals(block.input().get("restart"))) {
                  bashSession.restart();
                  result = "Bash session restarted";
              } else {
                  String command = (String) block.input().get("command");
                  result = bashSession.executeCommand(command);
              }

              // 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
              toolResults.add(Map.of("type", "tool_result", "tool_use_id", block.id(), "content", result));
          }
      }
      ```

      ```php PHP
      $toolResults = [];
      foreach ($response->content as $block) {
          if ($block->type === 'tool_use' && $block->name === 'bash') {
              if (!empty($block->input['restart'])) {
                  $bashSession->restart();
                  $result = 'Bash session restarted';
              } else {
                  $result = $bashSession->executeCommand($block->input['command']);
              }

              // 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
              $toolResults[] = ['type' => 'tool_result', 'tool_use_id' => $block->id, 'content' => $result];
          }
      }
      ```

      ```ruby Ruby
      tool_results = []
      response.content.each do |block|
        next unless block.type == "tool_use" && block.name == "bash"

        result =
          if block.input["restart"]
            bash_session.restart
            "Bash session restarted"
          else
            bash_session.execute_command(block.input["command"])
          end

        # 每个 tool_use 块对应一个 tool_result，全部在下一条用户消息中返回
        tool_results << {type: "tool_result", tool_use_id: block.id, content: result}
      end
      ```
    </CodeGroup>

  </Step>

  <Step title="将结果返回给 Claude">
    在继续同一对话的 `user` 消息中将 `tool_result` 发送回去。Claude 要么在同一会话中请求另一个命令，要么完成其回答：

    <CodeGroup>
      ```bash cURL
      curl https://api.anthropic.com/v1/messages \
        -H "content-type: application/json" \
        -H "x-api-key: $ANTHROPIC_API_KEY" \
        -H "anthropic-version: 2023-06-01" \
        -d '{
          "model": "claude-opus-4-8",
          "max_tokens": 1024,
          "tools": [
            {
              "type": "bash_20250124",
              "name": "bash"
            }
          ],
          "messages": [
            {
              "role": "user",
              "content": "List all Python files in the current directory."
            },
            {
              "role": "assistant",
              "content": [
                {
                  "type": "tool_use",
                  "id": "toolu_01A09q90qw90lq917835lq9",
                  "name": "bash",
                  "input": {
                    "command": "ls *.py"
                  }
                }
              ]
            },
            {
              "role": "user",
              "content": [
                {
                  "type": "tool_result",
                  "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
                  "content": "analysis.py\nprocess_data.py\n"
                }
              ]
            }
          ]
        }'
      ```

      ```bash CLI
      ant messages create <<'YAML'
      model: claude-opus-4-8
      max_tokens: 1024
      tools:
        - type: bash_20250124
          name: bash
      messages:
        - role: user
          content: List all Python files in the current directory.
        - role: assistant
          content:
            - type: tool_use
              id: toolu_01A09q90qw90lq917835lq9
              name: bash
              input:
                command: ls *.py
        - role: user
          content:
            - type: tool_result
              tool_use_id: toolu_01A09q90qw90lq917835lq9
              content: |
                analysis.py
                process_data.py
      YAML
      ```

      ```python Python
      client = anthropic.Anthropic()

      response = client.messages.create(
          model="claude-opus-4-8",
          max_tokens=1024,
          tools=[{"type": "bash_20250124", "name": "bash"}],
          messages=[
              {"role": "user", "content": "List all Python files in the current directory."},
              {
                  "role": "assistant",
                  "content": [
                      {
                          "type": "tool_use",
                          "id": "toolu_01A09q90qw90lq917835lq9",
                          "name": "bash",
                          "input": {"command": "ls *.py"},
                      }
                  ],
              },
              {
                  "role": "user",
                  "content": [
                      {
                          "type": "tool_result",
                          "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
                          "content": "analysis.py\nprocess_data.py\n",
                      }
                  ],
              },
          ],
      )

      print(response.content)
      ```

      ```typescript TypeScript
      const client = new Anthropic();

      const response = await client.messages.create({
        model: "claude-opus-4-8",
        max_tokens: 1024,
        tools: [{ type: "bash_20250124", name: "bash" }],
        messages: [
          {
            role: "user",
            content: "List all Python files in the current directory."
          },
          {
            role: "assistant",
            content: [
              {
                type: "tool_use",
                id: "toolu_01A09q90qw90lq917835lq9",
                name: "bash",
                input: { command: "ls *.py" }
              }
            ]
          },
          {
            role: "user",
            content: [
              {
                type: "tool_result",
                tool_use_id: "toolu_01A09q90qw90lq917835lq9",
                content: "analysis.py\nprocess_data.py\n"
              }
            ]
          }
        ]
      });

      console.log(response.content);
      ```

      ```csharp C#
      var client = new AnthropicClient();

      var response = await client.Messages.Create(
          new()
          {
              Model = Model.ClaudeOpus4_8,
              MaxTokens = 1024,
              Tools = [new ToolBash20250124()],
              Messages =
              [
                  new()
                  {
                      Role = Role.User,
                      Content = "List all Python files in the current directory.",
                  },
                  new()
                  {
                      Role = Role.Assistant,
                      Content = new MessageParamContent(new List<ContentBlockParam>
                      {
                          new ContentBlockParam(new ToolUseBlockParam()
                          {
                              ID = "toolu_01A09q90qw90lq917835lq9",
                              Name = "bash",
                              Input = new Dictionary<string, JsonElement>
                              {
                                  ["command"] = JsonSerializer.SerializeToElement("ls *.py"),
                              },
                          }),
                      }),
                  },
                  new()
                  {
                      Role = Role.User,
                      Content = new MessageParamContent(new List<ContentBlockParam>
                      {
                          new ContentBlockParam(new ToolResultBlockParam()
                          {
                              ToolUseID = "toolu_01A09q90qw90lq917835lq9",
                              Content = "analysis.py\nprocess_data.py\n",
                          }),
                      }),
                  },
              ],
          }
      );

      Console.WriteLine(response);
      ```

      ```go Go
      client := anthropic.NewClient()

      response, err := client.Messages.New(context.TODO(), anthropic.MessageNewParams{
      	Model:     anthropic.ModelClaudeOpus4_8,
      	MaxTokens: 1024,
      	Tools: []anthropic.ToolUnionParam{
      		{OfBashTool20250124: &anthropic.ToolBash20250124Param{}},
      	},
      	Messages: []anthropic.MessageParam{
      		anthropic.NewUserMessage(anthropic.NewTextBlock("List all Python files in the current directory.")),
      		anthropic.NewAssistantMessage(
      			anthropic.NewToolUseBlock(
      				"toolu_01A09q90qw90lq917835lq9",
      				map[string]any{"command": "ls *.py"},
      				"bash",
      			),
      		),
      		anthropic.NewUserMessage(
      			anthropic.NewToolResultBlock(
      				"toolu_01A09q90qw90lq917835lq9",
      				"analysis.py\nprocess_data.py\n",
      				false,
      			),
      		),
      	},
      })
      if err != nil {
      	log.Fatal(err)
      }
      fmt.Println(response.Content)
      ```

      ```java Java
      import com.anthropic.core.JsonValue;
      import com.anthropic.models.messages.ContentBlockParam;
      // ...
      import com.anthropic.models.messages.ToolBash20250124;
      import com.anthropic.models.messages.ToolResultBlockParam;
      import com.anthropic.models.messages.ToolUseBlockParam;
      // ...
      void main() {
          AnthropicClient client = AnthropicOkHttpClient.fromEnv();

          MessageCreateParams params = MessageCreateParams.builder()
              .model(Model.CLAUDE_OPUS_4_8)
              .maxTokens(1024)
              .addTool(ToolBash20250124.builder().build())
              .addUserMessage("List all Python files in the current directory.")
              .addAssistantMessageOfBlockParams(
                  List.of(
                      ContentBlockParam.ofToolUse(
                          ToolUseBlockParam.builder()
                              .id("toolu_01A09q90qw90lq917835lq9")
                              .name("bash")
                              .input(
                                  ToolUseBlockParam.Input.builder()
                                      .putAdditionalProperty("command", JsonValue.from("ls *.py"))
                                      .build()
                              )
                              .build()
                      )
                  )
              )
              .addUserMessageOfBlockParams(
                  List.of(
                      ContentBlockParam.ofToolResult(
                          ToolResultBlockParam.builder()
                              .toolUseId("toolu_01A09q90qw90lq917835lq9")
                              .content("analysis.py\nprocess_data.py\n")
                              .build()
                      )
                  )
              )
              .build();

          Message response = client.messages().create(params);
          IO.println(response.content());
      }
      ```

      ```php PHP
      use Anthropic\Messages\ToolBash20250124;

      $client = new Client();

      $response = $client->messages->create(
          model: 'claude-opus-4-8',
          maxTokens: 1024,
          tools: [new ToolBash20250124()],
          messages: [
              ['role' => 'user', 'content' => 'List all Python files in the current directory.'],
              [
                  'role' => 'assistant',
                  'content' => [
                      [
                          'type' => 'tool_use',
                          'id' => 'toolu_01A09q90qw90lq917835lq9',
                          'name' => 'bash',
                          'input' => ['command' => 'ls *.py'],
                      ],
                  ],
              ],
              [
                  'role' => 'user',
                  'content' => [
                      [
                          'type' => 'tool_result',
                          'tool_use_id' => 'toolu_01A09q90qw90lq917835lq9',
                          'content' => "analysis.py\nprocess_data.py\n",
                      ],
                  ],
              ],
          ],
      );

      print_r($response->content);
      ```

      ```ruby Ruby
      client = Anthropic::Client.new

      response = client.messages.create(
        model: "claude-opus-4-8",
        max_tokens: 1024,
        tools: [{type: "bash_20250124", name: "bash"}],
        messages: [
          {role: "user", content: "List all Python files in the current directory."},
          {
            role: "assistant",
            content: [
              {
                type: "tool_use",
                id: "toolu_01A09q90qw90lq917835lq9",
                name: "bash",
                input: {command: "ls *.py"}
              }
            ]
          },
          {
            role: "user",
            content: [
              {
                type: "tool_result",
                tool_use_id: "toolu_01A09q90qw90lq917835lq9",
                content: "analysis.py\nprocess_data.py\n"
              }
            ]
          }
        ]
      )

      puts response.content
      ```
    </CodeGroup>

    当 `stop_reason` 为 `tool_use` 时，重复运行并返回的循环。有关完整循环，请参阅[处理客户端工具的结果](/docs/zh-CN/agents-and-tools/tool-use/handle-tool-calls#handling-results-from-client-tools)。

  </Step>

  <Step title="实施安全措施">
    添加验证和限制。使用允许列表（allowlist）而不是阻止列表（blocklist）：阻止列表会遗漏任何它未预料到的命令。该示例还会拒绝作为独立单词出现的 shell 运算符：

    <CodeGroup exclude="shell">
      ```python Python
      import shlex

      ALLOWED_COMMANDS = {"ls", "cat", "echo", "pwd", "grep", "find", "wc", "head", "tail"}
      SHELL_OPERATORS = {"&&", "||", "|", ";", "&", ">", "<", ">>"}


      def validate_command(command):
          # 仅允许显式允许列表中的命令
          try:
              tokens = shlex.split(command)
          except ValueError:
              return False, "Could not parse command"

          if not tokens:
              return False, "Empty command"

          executable = tokens[0]
          if executable not in ALLOWED_COMMANDS:
              return False, f"Command '{executable}' is not in the allowlist"

          # 拒绝以独立单词形式书写的 shell 运算符
          for token in tokens[1:]:
              if token in SHELL_OPERATORS or token.startswith(("$", "`")):
                  return False, f"Shell operator '{token}' is not allowed"

          return True, None
      ```

      ```typescript TypeScript
      const ALLOWED_COMMANDS = new Set([
        "ls",
        "cat",
        "echo",
        "pwd",
        "grep",
        "find",
        "wc",
        "head",
        "tail"
      ]);
      const SHELL_OPERATORS = new Set(["&&", "||", "|", ";", "&", ">", "<", ">>"]);

      function validateCommand(command: string): { ok: boolean; reason?: string } {
        // 按空白字符拆分：足以进行触发检查
        const tokens = command.split(/\s+/).filter((token) => token.length > 0);
        if (tokens.length === 0) {
          return { ok: false, reason: "Empty command" };
        }

        // 仅允许显式允许列表中的命令
        const executable = tokens[0];
        if (!ALLOWED_COMMANDS.has(executable)) {
          return { ok: false, reason: `Command '${executable}' is not in the allowlist` };
        }

        // 拒绝以独立单词形式出现的 shell 运算符
        for (const token of tokens.slice(1)) {
          const bare = token.replace(/^["']+/, ""); // a quoted token can still smuggle an expansion
          if (SHELL_OPERATORS.has(token) || bare.startsWith("$") || bare.startsWith("`")) {
            return { ok: false, reason: `Shell operator '${token}' is not allowed` };
          }
        }

        return { ok: true };
      }
      ```

      ```csharp C#
      var allowedCommands = new HashSet<string>
      {
          "ls", "cat", "echo", "pwd", "grep", "find", "wc", "head", "tail"
      };
      var shellOperators = new HashSet<string> { "&&", "||", "|", ";", "&", ">", "<", ">>" };

      (bool Ok, string? Reason) ValidateCommand(string command)
      {
          // 按空白字符拆分：对于触发式检查已经足够
          var tokens = command.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
          if (tokens.Length == 0)
          {
              return (false, "Empty command");
          }

          // 仅允许显式允许列表（allowlist）中的命令
          var executable = tokens[0];
          if (!allowedCommands.Contains(executable))
          {
              return (false, $"Command '{executable}' is not in the allowlist");
          }

          // 拒绝以独立单词形式出现的 shell 运算符
          foreach (var token in tokens.Skip(1))
          {
              var bare = token.TrimStart('"', '\''); // a quoted token can still smuggle an expansion
              if (shellOperators.Contains(token) || bare.StartsWith('$') || bare.StartsWith('`'))
              {
                  return (false, $"Shell operator '{token}' is not allowed");
              }
          }

          return (true, null);
      }
      ```

      ```go Go
      var allowedCommands = map[string]bool{
      	"ls": true, "cat": true, "echo": true, "pwd": true, "grep": true,
      	"find": true, "wc": true, "head": true, "tail": true,
      }

      var shellOperators = map[string]bool{
      	"&&": true, "||": true, "|": true, ";": true, "&": true,
      	">": true, "<": true, ">>": true,
      }

      func validateCommand(command string) (bool, string) {
      	// 按空白字符拆分：足以用于触发式检查
      	tokens := strings.Fields(command)
      	if len(tokens) == 0 {
      		return false, "Empty command"
      	}

      	// 仅允许显式允许列表中的命令
      	executable := tokens[0]
      	if !allowedCommands[executable] {
      		return false, fmt.Sprintf("Command %q is not in the allowlist", executable)
      	}

      	// 拒绝以独立单词形式出现的 shell 运算符
      	for _, token := range tokens[1:] {
      		bare := strings.TrimLeft(token, `"'`) // a quoted token can still smuggle an expansion
      		if shellOperators[token] || strings.HasPrefix(bare, "$") || strings.HasPrefix(bare, "`") {
      			return false, fmt.Sprintf("Shell operator %q is not allowed", token)
      		}
      	}

      	return true, ""
      }
      ```

      ```java Java
      import java.util.List;
      import java.util.Set;

      static final Set<String> ALLOWED_COMMANDS =
          Set.of("ls", "cat", "echo", "pwd", "grep", "find", "wc", "head", "tail");
      static final Set<String> SHELL_OPERATORS = Set.of("&&", "||", "|", ";", "&", ">", "<", ">>");

      record Validation(boolean ok, String reason) {}

      Validation validateCommand(String command) {
          // 按空白字符拆分：足以用于触发式检查
          List<String> tokens = List.of(command.trim().split("\\s+"));
          if (tokens.size() == 1 && tokens.get(0).isEmpty()) {
              return new Validation(false, "Empty command");
          }

          // 仅允许显式允许列表中的命令
          String executable = tokens.get(0);
          if (!ALLOWED_COMMANDS.contains(executable)) {
              return new Validation(false, "Command '" + executable + "' is not in the allowlist");
          }

          // 拒绝以独立单词形式出现的 shell 运算符
          for (String token : tokens.subList(1, tokens.size())) {
              String bare = token.replaceFirst("^[\"']+", ""); // a quoted token can still smuggle an expansion
              if (SHELL_OPERATORS.contains(token) || bare.startsWith("$") || bare.startsWith("`")) {
                  return new Validation(false, "Shell operator '" + token + "' is not allowed");
              }
          }

          return new Validation(true, null);
      }
      ```

      ```php PHP
      const ALLOWED_COMMANDS = ['ls', 'cat', 'echo', 'pwd', 'grep', 'find', 'wc', 'head', 'tail'];
      const SHELL_OPERATORS = ['&&', '||', '|', ';', '&', '>', '<', '>>'];

      function validateCommand(string $command): array
      {
          // 按空白字符拆分：足以用于触发式检查
          $tokens = preg_split('/\\s+/', trim($command), -1, PREG_SPLIT_NO_EMPTY);
          if ($tokens === false || $tokens === []) {
              return [false, 'Empty command'];
          }

          // 仅允许显式允许列表中的命令
          $executable = $tokens[0];
          if (!in_array($executable, ALLOWED_COMMANDS, true)) {
              return [false, "Command '{$executable}' is not in the allowlist"];
          }

          // 拒绝以独立单词形式出现的 shell 运算符
          foreach (array_slice($tokens, 1) as $token) {
              $bare = ltrim($token, '"\''); // a quoted token can still smuggle an expansion
              if (in_array($token, SHELL_OPERATORS, true) || str_starts_with($bare, '$') || str_starts_with($bare, '`')) {
                  return [false, "Shell operator '{$token}' is not allowed"];
              }
          }

          return [true, null];
      }
      ```

      ```ruby Ruby
      require "shellwords"

      ALLOWED_COMMANDS = %w[ls cat echo pwd grep find wc head tail].freeze
      SHELL_OPERATORS = ["&&", "||", "|", ";", "&", ">", "<", ">>"].freeze

      def validate_command(command)
        # 仅允许显式允许列表中的命令
        begin
          tokens = Shellwords.split(command)
        rescue ArgumentError
          return [false, "Could not parse command"]
        end

        return [false, "Empty command"] if tokens.empty?

        executable = tokens[0]
        unless ALLOWED_COMMANDS.include?(executable)
          return [false, "Command '#{executable}' is not in the allowlist"]
        end

        # 拒绝以独立单词形式书写的 shell 运算符
        tokens[1..].each do |token|
          if SHELL_OPERATORS.include?(token) || token.start_with?("$", "`")
            return [false, "Shell operator '#{token}' is not allowed"]
          end
        end

        [true, nil]
      end
      ```
    </CodeGroup>

    此检查是针对明显错误的警示线，而不是强制边界。它会拒绝本页其他示例中使用的带空格的链式调用（`&&`）、管道和重定向。它无法捕获紧贴在单词上的运算符，例如 `cat data.txt|grep x`，因为分词器会将 `data.txt|grep` 保留在一个令牌中。请决定您的应用程序允许哪些命令和运算符。真正的控制手段是隔离：在容器或虚拟机中运行整个会话（请参阅[安全性](#security)）。

  </Step>
</Steps>

### 处理错误

当命令失败或会话中断时，请告知 Claude 发生了什么。将消息作为 `tool_result` 内容返回，并将 `is_error` 设置为 `true`，这会将该工具调用标记为失败。请参阅[使用 is\_error 处理错误](/docs/zh-CN/agents-and-tools/tool-use/handle-tool-calls#handling-errors-with-is-error)。

<AccordionGroup>
  <Accordion title="命令执行超时">
    如果命令执行时间过长：

    ```json
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
          "content": "Error: command did not finish within 30 seconds",
          "is_error": true
        }
      ]
    }
    ```

  </Accordion>

  <Accordion title="命令未找到">
    如果命令不存在：

    ```json
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
          "content": "bash: nonexistentcommand: command not found",
          "is_error": true
        }
      ]
    }
    ```

  </Accordion>

  <Accordion title="权限被拒绝">
    如果存在权限问题：

    ```json
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
          "content": "bash: /root/sensitive-file: Permission denied",
          "is_error": true
        }
      ]
    }
    ```

  </Accordion>
</AccordionGroup>

### 遵循实现最佳实践

<AccordionGroup>
  <Accordion title="使用命令超时">
    永远不会结束的命令（例如等待输入的命令）会永久阻塞会话，因为其哨兵行永远不会到达。请为每个命令设置截止时间。当超过截止时间时，停止 shell 以及该命令启动的所有内容，然后重启会话：

    <CodeGroup exclude="shell">
      ```python Python
      import concurrent.futures
      import os
      import signal


      def execute_with_timeout(session, command, timeout=30):
          """Run a command in the session, replacing the session if the command hangs."""
          with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
              future = pool.submit(session.execute_command, command)
              try:
                  return future.result(timeout=timeout)
              except concurrent.futures.TimeoutError:
                  # 该进程组即 shell 以及该命令启动的所有进程
                  os.killpg(session.process.pid, signal.SIGKILL)
                  session.restart()
                  return f"Error: command did not finish within {timeout} seconds"
      ```

      ```typescript TypeScript
      // 在会话中运行命令，如果命令挂起则替换该会话。
      async function executeWithTimeout(
        session: BashSession,
        command: string,
        timeoutMs = 30000
      ): Promise<string> {
        let timer: NodeJS.Timeout | undefined;
        const timedOut = new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error("timeout")), timeoutMs);
        });
        try {
          return await Promise.race([session.executeCommand(command), timedOut]);
        } catch {
          // 该进程组是 shell 以及该命令启动的所有进程
          if (session.process.pid !== undefined) {
            process.kill(-session.process.pid, "SIGKILL");
          }
          session.restart();
          return `Error: command did not finish within ${timeoutMs / 1000} seconds`;
        } finally {
          clearTimeout(timer);
        }
      }
      ```

      ```csharp C#
      using System.Diagnostics;

      // 在会话中运行命令，如果命令挂起则替换该会话。
      static string ExecuteWithTimeout(BashSession session, string command, int timeoutSeconds = 30)
      {
          var work = Task.Run(() => session.ExecuteCommand(command));
          if (work.Wait(TimeSpan.FromSeconds(timeoutSeconds)))
          {
              return work.Result;
          }

          // 停止 shell 及其启动的所有进程，然后启动一个新会话
          session.Process.Kill(entireProcessTree: true);
          session.Restart();
          return $"Error: command did not finish within {timeoutSeconds} seconds";
      }
      ```

      ```go Go
      // executeWithTimeout 运行一条命令，如果命令挂起则替换会话。
      func executeWithTimeout(session *BashSession, command string, timeoutSeconds int) string {
      	done := make(chan string, 1)
      	go func() { done <- session.ExecuteCommand(command) }()

      	select {
      	case result := <-done:
      		return result
      	case <-time.After(time.Duration(timeoutSeconds) * time.Second):
      		// 该进程组包括 shell 以及该命令启动的所有进程
      		syscall.Kill(-session.cmd.Process.Pid, syscall.SIGKILL)
      		session.Restart()
      		return fmt.Sprintf("Error: command did not finish within %d seconds", timeoutSeconds)
      	}
      }
      ```

      ```java Java
      // 在会话中运行命令，如果命令挂起则替换该会话。
      String executeWithTimeout(BashSession session, String command, int timeoutSeconds) throws Exception {
          ExecutorService pool = Executors.newSingleThreadExecutor();
          try {
              Future<String> future = pool.submit(() -> session.executeCommand(command));
              return future.get(timeoutSeconds, TimeUnit.SECONDS);
          } catch (TimeoutException e) {
              // 停止 shell 及其启动的所有进程，然后启动一个新会话
              session.process.descendants().forEach(ProcessHandle::destroyForcibly);
              session.process.destroyForcibly();
              session.restart();
              return "Error: command did not finish within " + timeoutSeconds + " seconds";
          } finally {
              pool.shutdownNow();
          }
      }
      ```

      ```php PHP
      // 运行命令，但如果在截止时间内未完成则放弃。PHP 在读取管道时
      // 会阻塞，因此截止时间的检查位于读取循环内：stream_select() 在每次
      // fgets() 之前等待可读输出，使循环能够检查截止时间。
      function executeWithTimeout(BashSession $session, string $command, int $timeout = 30): string
      {
          $sentinel = '__CLAUDE_BASH_DONE_' . bin2hex(random_bytes(16)) . '__'; // unique per call
          fwrite($session->stdin, "{$command}\necho {$sentinel}\n");
          fflush($session->stdin);

          $deadline = microtime(true) + $timeout;
          $output = '';
          while (microtime(true) < $deadline) {
              $read = [$session->output];
              $write = null;
              $except = null;
              if (stream_select($read, $write, $except, 1) === 0) {
                  continue; // no output yet; check the deadline again
              }
              $line = fgets($session->output);
              if ($line === false || str_contains($line, $sentinel)) {
                  return $output; // this command's output is complete
              }
              $output .= $line;
          }

          // 该进程组包括 shell 以及该命令启动的所有进程
          posix_kill(-proc_get_status($session->process)['pid'], 9); // 9 = SIGKILL
          $session->restart();
          return "Error: command did not finish within {$timeout} seconds";
      }
      ```

      ```ruby Ruby
      require "timeout"

      # 在会话中运行命令，如果命令挂起则替换该会话。
      def execute_with_timeout(session, command, timeout: 30)
        Timeout.timeout(timeout) { session.execute_command(command) }
      rescue Timeout::Error
        # 该进程组即 shell 以及该命令启动的所有进程
        Process.kill("KILL", -session.wait_thread.pid)
        session.restart
        "Error: command did not finish within #{timeout} seconds"
      end
      ```
    </CodeGroup>

    终止操作会停止挂起的命令及其启动的所有内容。将消息作为错误 `tool_result` 返回（请参阅[处理错误](#handle-errors)），这会将该工具调用标记为失败。

  </Accordion>

  <Accordion title="维护会话状态">
    保持 bash 会话的持久性，以维护环境变量和工作目录：

    <CodeGroup exclude="shell">
      ```python Python
      # 在同一会话中运行的命令会保持状态
      commands = [
          "cd /tmp",
          "echo 'Hello' > test.txt",
          "cat test.txt",  # The session is still in /tmp
      ]
      ```

      ```typescript TypeScript
      // 在同一会话中运行的命令会保持状态
      const commands = [
        "cd /tmp",
        "echo 'Hello' > test.txt",
        "cat test.txt" // The session is still in /tmp
      ];
      ```

      ```csharp C#
      // 在同一会话中运行的命令会保持状态
      string[] commands =
      [
          "cd /tmp",
          "echo 'Hello' > test.txt",
          "cat test.txt", // The session is still in /tmp
      ];
      ```

      ```go Go
      // 在同一会话中运行的命令会保持状态
      commands := []string{
      	"cd /tmp",
      	"echo 'Hello' > test.txt",
      	"cat test.txt", // The session is still in /tmp
      }
      ```

      ```java Java
      // 在同一会话中运行的命令会保持状态
      List<String> commands = List.of(
          "cd /tmp",
          "echo 'Hello' > test.txt",
          "cat test.txt" // The session is still in /tmp
      );
      ```

      ```php PHP
      // 在同一会话中运行的命令会保持状态
      $commands = [
          'cd /tmp',
          "echo 'Hello' > test.txt",
          'cat test.txt', // The session is still in /tmp
      ];
      ```

      ```ruby Ruby
      # 在同一会话中运行的命令会保持状态
      commands = [
        "cd /tmp",
        "echo 'Hello' > test.txt",
        "cat test.txt" # The session is still in /tmp
      ]
      ```
    </CodeGroup>

  </Accordion>

  <Accordion title="处理大型输出">
    截断大型输出以防止令牌限制问题：

    <CodeGroup exclude="shell">
      ```python Python
      def truncate_output(output, max_lines=100):
          lines = output.split("\n")
          if len(lines) > max_lines:
              truncated = "\n".join(lines[:max_lines])
              return f"{truncated}\n\n... Output truncated ({len(lines)} total lines) ..."
          return output
      ```

      ```typescript TypeScript
      function truncateOutput(output: string, maxLines = 100): string {
        const lines = output.split("\n");
        if (lines.length > maxLines) {
          const truncated = lines.slice(0, maxLines).join("\n");
          return `${truncated}\n\n... Output truncated (${lines.length} total lines) ...`;
        }
        return output;
      }
      ```

      ```csharp C#
      string TruncateOutput(string output, int maxLines = 100)
      {
          var lines = output.Split('\n');
          if (lines.Length > maxLines)
          {
              var truncated = string.Join("\n", lines.Take(maxLines));
              return $"{truncated}\n\n... Output truncated ({lines.Length} total lines) ...";
          }
          return output;
      }
      ```

      ```go Go
      func truncateOutput(output string, maxLines int) string {
      	lines := strings.Split(output, "\n")
      	if len(lines) > maxLines {
      		truncated := strings.Join(lines[:maxLines], "\n")
      		return fmt.Sprintf("%s\n\n... Output truncated (%d total lines) ...", truncated, len(lines))
      	}
      	return output
      }
      ```

      ```java Java
      String truncateOutput(String output, int maxLines) {
          String[] lines = output.split("\n", -1);
          if (lines.length > maxLines) {
              String truncated = String.join("\n", Arrays.copyOf(lines, maxLines));
              return truncated + "\n\n... Output truncated (" + lines.length + " total lines) ...";
          }
          return output;
      }
      ```

      ```php PHP
      function truncateOutput(string $output, int $maxLines = 100): string
      {
          $lines = explode("\n", $output);
          if (count($lines) > $maxLines) {
              $truncated = implode("\n", array_slice($lines, 0, $maxLines));
              return "{$truncated}\n\n... Output truncated (" . count($lines) . ' total lines) ...';
          }
          return $output;
      }
      ```

      ```ruby Ruby
      def truncate_output(output, max_lines: 100)
        lines = output.split("\n", -1)
        return output unless lines.length > max_lines

        truncated = lines.first(max_lines).join("\n")
        "#{truncated}\n\n... Output truncated (#{lines.length} total lines) ..."
      end
      ```
    </CodeGroup>

  </Accordion>

  <Accordion title="记录所有命令">
    保留审计跟踪。将每个命令都通过一个包装器路由，该包装器在命令运行前记录命令，并在命令完成后记录输出。即使命令挂起或破坏了会话，也仍会留下记录：

    <CodeGroup exclude="shell">
      ```python Python
      import logging

      logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


      def execute_and_log(session, command):
          """Run a command in the session and keep an audit record of it."""
          logging.info("command=%r", command)
          output = session.execute_command(command)
          logging.info("output=%r", output[:200])  # first 200 characters
          return output
      ```

      ```typescript TypeScript
      // 在会话中运行命令并保留其审计记录。
      async function executeAndLog(session: BashSession, command: string): Promise<string> {
        console.error(`command=${JSON.stringify(command)}`);
        const output = await session.executeCommand(command);
        console.error(`output=${JSON.stringify(output.slice(0, 200))}`); // first 200 characters
        return output;
      }
      ```

      ```csharp C#
      // 在会话中运行命令并保留其审计记录。
      static string ExecuteAndLog(BashSession session, string command)
      {
          Console.Error.WriteLine($"command={command}");
          var output = session.ExecuteCommand(command);
          Console.Error.WriteLine($"output={output[..Math.Min(output.Length, 200)]}"); // first 200 characters
          return output;
      }
      ```

      ```go Go
      // executeAndLog 在会话中运行命令并保留其审计记录。
      func executeAndLog(session *BashSession, command string) string {
      	log.Printf("command=%q", command)
      	output := session.ExecuteCommand(command)
      	log.Printf("output=%q", output[:min(len(output), 200)]) // first 200 characters
      	return output
      }
      ```

      ```java Java
      static final Logger AUDIT = Logger.getLogger("bash-audit");

      // 在会话中运行命令并保留其审计记录。
      String executeAndLog(BashSession session, String command) throws IOException {
          AUDIT.info("command=" + command);
          String output = session.executeCommand(command);
          AUDIT.info("output=" + output.substring(0, Math.min(output.length(), 200))); // first 200 characters
          return output;
      }
      ```

      ```php PHP
      // 在会话中运行命令并保留其审计记录。
      function executeAndLog(BashSession $session, string $command): string
      {
          error_log("command={$command}");
          $output = $session->executeCommand($command);
          error_log('output=' . substr($output, 0, 200)); // first 200 characters
          return $output;
      }
      ```

      ```ruby Ruby
      require "logger"

      AUDIT = Logger.new($stderr)

      # 在会话中运行命令并保留其审计记录。
      def execute_and_log(session, command)
        AUDIT.info("command=#{command.inspect}")
        output = session.execute_command(command)
        AUDIT.info("output=#{output[0, 200].inspect}") # first 200 characters
        output
      end
      ```
    </CodeGroup>

    记录默认输出到 `stderr`；请将它们指向文件或您的日志管道以便保留。请包含能将记录与您应用程序中的请求关联起来的任何信息，例如最终用户和 `tool_use_id`。

  </Accordion>
</AccordionGroup>

## 安全性

<Warning>
  您的应用程序会运行 Claude 请求的任何命令。请在隔离环境（例如容器或虚拟机）中以能够完成工作的最低权限用户身份运行会话。将每个命令都视为不可信输入。
</Warning>

除了隔离之外，还应添加以下控制措施：

- 在运行命令之前验证命令，使用允许列表而不是阻止列表。请参阅[实现 bash 工具](#implement-the-bash-tool)。
- 为 shell 进程设置资源限制（CPU、内存和磁盘），例如使用 `ulimit`。
- 记录每个命令及其输出，以便您可以审计运行了什么。
- 在将输出返回给 Claude 之前，从输出中删除凭据和其他机密信息。

## 定价

bash 工具定义会为您的请求添加以下输入令牌。这是在每个模型的[工具使用系统提示](/docs/zh-CN/agents-and-tools/tool-use/overview#pricing)之外的额外消耗，后者在存在任何工具时都会生效。

| 模型                                          | 额外输入令牌 |
| --------------------------------------------- | ------------ |
| Claude Opus 4.7 和 Claude Opus 4.8            | 325 个令牌   |
| Claude Opus 4.6、Claude Sonnet 4.6 及更早版本 | 244 个令牌   |

以下内容会消耗额外的令牌：

- 命令输出（stdout/stderr）
- 错误消息
- 大型文件内容

有关完整的定价详情，请参阅[工具使用定价](/docs/zh-CN/agents-and-tools/tool-use/overview#pricing)。

## 常见模式

### 开发工作流

- 运行测试：`pytest && coverage report`
- 构建项目：`npm install && npm run build`
- Git 操作：`git status && git add . && git commit -m "message"`

有关在长时间运行的代理工作流中将 git 用作检查点和恢复机制的指导，请参阅[状态管理最佳实践](/docs/zh-CN/build-with-claude/prompt-engineering/claude-prompting-best-practices#state-management-best-practices)。

### 文件操作

- 处理数据：`wc -l *.csv && ls -lh *.csv`
- 搜索文件：`find . -name "*.py" | xargs grep "pattern"`
- 创建备份：`tar -czf backup.tar.gz ./data`

### 系统任务

- 检查资源：`df -h && free -m`
- 进程管理：`ps aux | grep python`
- 环境设置：`export PATH=$PATH:/new/path && echo $PATH`

## 限制

- **不支持交互式命令：** 会话无法运行 `vim`、`less`、密码提示或任何在 stdin 上等待输入的命令。
- **不支持 GUI 应用程序：** 会话仅限命令行。
- **会话范围：** Bash 会话状态位于客户端。您的应用程序负责在轮次之间维护 shell 会话。
- **输出限制：** API 不会截断工具结果（过大的请求会被拒绝）。请在将大型输出返回给 Claude 之前在您的应用程序中截断它们。
- **不支持流式传输：** 只有当您的应用程序在下一个请求中返回 `tool_result` 时，输出才会到达 Claude。

## 与其他工具结合使用

Bash 工具与[文本编辑器工具](/docs/zh-CN/agents-and-tools/tool-use/text-editor-tool)配合良好：Claude 使用一个工具编辑文件，并使用另一个工具请求运行该文件的命令。

<Note>
  如果您还在使用[代码执行工具](/docs/zh-CN/agents-and-tools/tool-use/code-execution-tool)，Claude 可以访问两个独立的执行环境：您的本地 bash 会话和 Anthropic 的沙盒容器。它们之间不共享状态。有关提示 Claude 区分不同环境的指导，请参阅[将代码执行与其他执行工具一起使用](/docs/zh-CN/agents-and-tools/tool-use/code-execution-tool#using-code-execution-with-other-execution-tools)。
</Note>

## 后续步骤

<CardGroup cols={2}>
  <Card title="文本编辑器工具" icon="file" href="/docs/zh-CN/agents-and-tools/tool-use/text-editor-tool">
    查看和修改文本文件，以调试、修复和改进代码。
  </Card>

  <Card title="Claude 的工具使用" icon="tool" href="/docs/zh-CN/agents-and-tools/tool-use/overview">
    将 Claude 连接到外部工具和 API。了解工具在哪里执行、Claude 何时调用它们，以及哪个工具适合您的任务。
  </Card>
</CardGroup>
