from periphery import GPIO

class GPIOSwitch:
	def __init__(self, line: int = 37, active_high: bool = True, chip: str = "/dev/gpiochip4"):
		self.line = line
		self.active_high = active_high
		self.chip = chip
		self._gpio = None
		self._state = False
		try:
			self._gpio = GPIO(self.chip, self.line, "out")
			# 初始化为断电（关闭）状态
			self.set_state(False)
		except Exception as e:
			raise RuntimeError(f"无法打开 GPIO {self.chip} line {self.line}: {e}")

	def set_state(self, on: bool):
		# 根据 active_high 决定写入的逻辑电平
		if self._gpio is None:
			raise RuntimeError("GPIO 未初始化")
		# 当 active_high == True 时: on -> 高电平(True), off -> 低电平(False)
		hw_value = True if (on and self.active_high) or (not on and not self.active_high) else False
		try:
			self._gpio.write(hw_value)
			self._state = on
		except Exception as e:
			raise RuntimeError(f"写入 GPIO 失败: {e}")

	def open(self):
		"""便捷方法：打开供电（高电平）"""
		self.set_state(True)

	def close(self):
		"""便捷方法：关闭供电（低电平）。
		注意：此方法会把输出设为断电态，但不会释放底层 GPIO 资源。
		"""
		self.set_state(False)

	def get_state(self) -> bool:
		# 优先读取硬件实际电平，返回布尔表示的逻辑状态（on/off）
		if self._gpio is None:
			raise RuntimeError("GPIO 未初始化")
		try:
			hw = self._gpio.read()
		except Exception:
			# 读取失败时回退到缓存状态
			return self._state
		# hw 为实际电平（True 表示高电平），根据 active_high 映射到逻辑状态
		return True if (hw and self.active_high) or (not hw and not self.active_high) else False

	def destroy(self):
		"""释放底层 GPIO 资源。调用前会尝试把输出置为安全态（断电）。"""
		if self._gpio is None:
			return
		try:
			# 先把输出设为断电态
			try:
				self.set_state(False)
			except Exception:
				pass
			self._gpio.close()
		finally:
			self._gpio = None

