---@diagnostic disable: undefined-global
-- pstack actions for Rex (`rex` is the global Rex gives its config). init.lua loads this file through the block `plugins/pstack-action.py install-rex` writes:
--   dofile("<checkout>/plugins/rex/pstack.lua")("<checkout>")
return function(root)
  local script = root .. "/plugins/pstack-action.py"

  -- Types `text` into the agent in the block the action ran from.
  local function prompt(text)
    return function(ctx)
      local r, err = rex.exec(
        { "python3", script, "prompt", "--target", ctx.block_id, text },
        { env = { PSTACK_MUX = "rex" } }
      )
      if not r then
        return { ok = false, error = err }
      end
      return { ok = r.ok, output = r.stdout .. r.stderr }
    end
  end

  for _, w in ipairs({ "kitchen", "pair", "trio" }) do
    rex.action{
      name = "pstack_" .. w,
      title = "pstack: start a " .. w .. " in this block",
      category = "pstack",
      run = prompt("/pstack-" .. w),
    }
  end

  rex.action{
    name = "pstack_fleet",
    title = "pstack: show every kitchen run",
    category = "pstack",
    run = function()
      return rex.session.new_window{
        layout = rex.layout.block{
          flavor = "com.superlogical.terminal.shell",
          options = {
            command = { "python3", script, "fleet" },
            exit = { on_completion = false },
          },
        },
      }
    end,
  }

  rex.action{
    name = "pstack_sync",
    title = "pstack: link the skills into each agent",
    category = "pstack",
    run = function()
      local r, err = rex.exec({ "python3", script, "sync" })
      if not r then
        return { ok = false, error = err }
      end
      return { ok = r.ok, output = r.stdout .. r.stderr }
    end,
  }
end
