"""P 系列 GPU 冒烟测试：在真实 GPU（RTX 3050）上跑一个最小 compute shader。

验证 wgpu 依赖装好后可用的完整链路（wgpu 0.32 Python 绑定 API）：
  1. 请求 adapter + device（真实 GPU 硬件路径）
  2. 建 storage buffer + 编译 WGSL shader（keyword-only API + 真实 struct 类）
  3. dispatch 1 个 workgroup（4 线程），把 4 个 uint 都写成 42
  4. 读回结果验证

wgpu 0.32 结构体用法：*Struct 后缀是 Union（类 | dict），实际用类
（BindGroupLayoutEntry / BufferBindingLayout / ProgrammableStage /
BindGroupEntry / ComputePassDescriptor），或用普通 dict。
"""
import sys
import numpy as np
import wgpu
from wgpu import structs, enums


def main():
    gpu = wgpu.gpu
    adapter = gpu.request_adapter_sync()
    assert adapter is not None, "没有可用 GPU 适配器"
    print("adapter:", adapter.info.get("device"),
          "| backend:", adapter.info.get("backend_type"))

    device = adapter.request_device_sync(required_features=[])
    assert device is not None, "device 创建失败"
    print("device OK")

    # 1. 编译 WGSL shader
    shader = """
    @group(0) @binding(0) var<storage, read_write> out: array<u32>;

    @compute @workgroup_size(4)
    fn main(@builtin(global_invocation_id) id: vec3<u32>) {
        out[id.x] = 42u;
    }
    """
    module = device.create_shader_module(code=shader)
    print("shader module compiled OK")

    # 2. storage buffer
    out_buf = device.create_buffer(
        size=16,
        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
    )
    print("storage buffer created:", out_buf is not None)

    # 3. bind group layout + pipeline（用真实 struct 类）
    bind_group_layout = device.create_bind_group_layout(
        entries=[structs.BindGroupLayoutEntry(
            binding=0,
            visibility=wgpu.ShaderStage.COMPUTE,
            buffer=structs.BufferBindingLayout(
                type=enums.BufferBindingType['storage']),
        )]
    )

    pipeline = device.create_compute_pipeline(
        layout=device.create_pipeline_layout(
            bind_group_layouts=[bind_group_layout]),
        compute=structs.ProgrammableStage(
            module=module, entry_point="main"),
    )
    print("compute pipeline OK")

    bind_group = device.create_bind_group(
        layout=bind_group_layout,
        entries=[structs.BindGroupEntry(binding=0, resource=out_buf)],
    )

    # 4. dispatch + submit
    enc = device.create_command_encoder()
    cp = enc.begin_compute_pass()
    cp.set_pipeline(pipeline)
    cp.set_bind_group(0, bind_group)
    cp.dispatch_workgroups(1)
    cp.end()
    cmds = enc.finish()
    device.queue.submit([cmds])
    print("compute dispatched + submitted")

    # 5. 读回（wgpu 0.32 用 queue.read_buffer）
    np_out = np.frombuffer(
        device.queue.read_buffer(out_buf, 0, 16), dtype=np.uint32)
    print("read back:", np_out.tolist())
    assert np.all(np_out == 42), f"期望全 42，实际 {np_out.tolist()}"
    print("SMOKE TEST PASSED — GPU compute 真正执行了")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("SMOKE TEST FAILED:", type(e).__name__, e)
        sys.exit(1)
