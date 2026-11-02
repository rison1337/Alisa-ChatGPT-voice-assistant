print("Alisa GPT open trigger is waiting for: открой чат")

while true do
  local ok, err = pcall(function()
    alice.command.listen("открой чат", function(message)
      print("Matched command: открой чат")
      alice.command.cancel()
      local output = alice.exec("action.sh", {"start"})
      if output and output ~= "" then print(output) end
    end)
  end)
  if not ok then
    print("Command listener reconnecting: " .. tostring(err))
    alice.sleep(1000)
  end
end
