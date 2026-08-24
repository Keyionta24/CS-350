"""
CS 350 Final Project - Smart Thermostat Prototype

The thermostat uses three states: off, heat, and cool. It reads the AHT20
temperature sensor over I2C, controls two PWM LEDs, handles three GPIO button
interrupts, updates a 16x2 LCD, and transmits a comma-delimited status message
over UART every 30 seconds.
"""

from datetime import datetime
from math import floor
from threading import Thread
from time import sleep

import adafruit_ahtx0
import adafruit_character_lcd.character_lcd as characterlcd
import board
import digitalio
import serial
from gpiozero import Button, PWMLED
from statemachine import State, StateMachine


DEBUG = True


# Initialize the AHT20 temperature sensor on the I2C bus.
i2c = board.I2C()
thSensor = adafruit_ahtx0.AHTx0(i2c)


# Initialize UART using 115200 baud, 8 data bits, no parity, and one stop bit.
ser = serial.Serial(
    port="/dev/ttyS0",
    baudrate=115200,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=1,
)


# PWM LEDs used to represent heating and cooling.
redLight = PWMLED(18)
blueLight = PWMLED(23)


class ManagedDisplay:
    """Manage the 16x2 LCD and its GPIO connections."""

    def __init__(self):
        self.lcd_rs = digitalio.DigitalInOut(board.D17)
        self.lcd_en = digitalio.DigitalInOut(board.D27)
        self.lcd_d4 = digitalio.DigitalInOut(board.D5)
        self.lcd_d5 = digitalio.DigitalInOut(board.D6)
        self.lcd_d6 = digitalio.DigitalInOut(board.D13)
        self.lcd_d7 = digitalio.DigitalInOut(board.D26)

        self.lcd_columns = 16
        self.lcd_rows = 2

        self.lcd = characterlcd.Character_LCD_Mono(
            self.lcd_rs,
            self.lcd_en,
            self.lcd_d4,
            self.lcd_d5,
            self.lcd_d6,
            self.lcd_d7,
            self.lcd_columns,
            self.lcd_rows,
        )
        self.lcd.clear()

    def cleanupDisplay(self):
        """Clear the LCD and release all GPIO lines used by it."""
        self.lcd.clear()
        self.lcd_rs.deinit()
        self.lcd_en.deinit()
        self.lcd_d4.deinit()
        self.lcd_d5.deinit()
        self.lcd_d6.deinit()
        self.lcd_d7.deinit()

    def clear(self):
        """Clear the LCD."""
        self.lcd.clear()

    def updateScreen(self, message):
        """Replace the current LCD message."""
        self.lcd.clear()
        self.lcd.message = message


screen = ManagedDisplay()


class TemperatureMachine(StateMachine):
    """State machine for the off, heat, and cool thermostat states."""

    off = State(initial=True)
    heat = State()
    cool = State()

    # The thermostat begins at the required 72-degree Fahrenheit set point.
    setPoint = 72

    # State sequence: off -> heat -> cool -> off.
    cycle = off.to(heat) | heat.to(cool) | cool.to(off)

    def getCurrentStateId(self):
        """Return the active state ID from the current configuration."""
        return next(iter(self.configuration)).id

    def on_enter_heat(self):
        """Update the indicators when the heat state becomes active."""
        self.updateLights()
        if DEBUG:
            print("* Changing state to heat")

    def on_exit_heat(self):
        """Turn off the heat indicator when leaving the heat state."""
        redLight.off()

    def on_enter_cool(self):
        """Update the indicators when the cool state becomes active."""
        self.updateLights()
        if DEBUG:
            print("* Changing state to cool")

    def on_exit_cool(self):
        """Turn off the cooling indicator when leaving the cool state."""
        blueLight.off()

    def on_enter_off(self):
        """Turn off both indicators when the thermostat is off."""
        redLight.off()
        blueLight.off()
        if DEBUG:
            print("* Changing state to off")

    def processTempStateButton(self):
        """Cycle the state machine when the GPIO 24 button is pressed."""
        if DEBUG:
            print("Cycling Temperature State")
        self.send("cycle")

    def processTempIncButton(self):
        """Increase the set point by one degree and refresh the LEDs."""
        if DEBUG:
            print("Increasing Set Point")
        self.setPoint += 1
        self.updateLights()

    def processTempDecButton(self):
        """Decrease the set point by one degree and refresh the LEDs."""
        if DEBUG:
            print("Decreasing Set Point")
        self.setPoint -= 1
        self.updateLights()

    def updateLights(self):
        """Set the LED behavior based on state, room temperature, and set point."""
        current_temp = floor(self.getFahrenheit())

        # Stop any earlier solid or pulsing output before setting the new output.
        redLight.off()
        blueLight.off()

        if DEBUG:
            print(f"State: {self.getCurrentStateId()}")
            print(f"SetPoint: {self.setPoint}")
            print(f"Temp: {current_temp}")

        if self.getCurrentStateId() == "heat":
            if current_temp < self.setPoint:
                redLight.pulse()
            else:
                redLight.on()
        elif self.getCurrentStateId() == "cool":
            if current_temp > self.setPoint:
                blueLight.pulse()
            else:
                blueLight.on()

    def run(self):
        """Start LCD and UART management in a separate thread."""
        self.displayThread = Thread(target=self.manageMyDisplay, daemon=True)
        self.displayThread.start()

    def getFahrenheit(self):
        """Read the AHT20 sensor and return Fahrenheit."""
        celsius = thSensor.temperature
        return ((9 / 5) * celsius) + 32

    def setupSerialOutput(self):
        """Create the required state,temperature,set-point UART message."""
        return (
            f"{self.getCurrentStateId()},"
            f"{self.getFahrenheit():.1f},"
            f"{self.setPoint}\n"
        )

    endDisplay = False

    def manageMyDisplay(self):
        """Update the LCD each second and send UART status every 30 seconds."""
        counter = 1
        altCounter = 1

        while not self.endDisplay:
            if DEBUG:
                print("Processing Display Info...")

            current_time = datetime.now()

            # Exactly 16 visible characters before the newline.
            lcd_line_1 = current_time.strftime("%b %d  %H:%M:%S\n")

            # Show temperature for five seconds, then state/set point for five.
            if altCounter < 6:
                lcd_line_2 = f"Temp: {self.getFahrenheit():.1f}F"
                altCounter += 1
            else:
                state_name = self.getCurrentStateId().title()
                lcd_line_2 = f"{state_name} Set: {self.setPoint}F"
                altCounter += 1

                if altCounter >= 11:
                    self.updateLights()
                    altCounter = 1

            screen.updateScreen(lcd_line_1 + lcd_line_2)

            if DEBUG:
                print(f"Counter: {counter}")

            # Transmit one comma-delimited status line every 30 seconds.
            if (counter % 30) == 0:
                output = self.setupSerialOutput()
                ser.write(output.encode("utf-8"))
                if DEBUG:
                    print(f"UART: {output.strip()}")
                counter = 1
            else:
                counter += 1

            sleep(1)

        screen.cleanupDisplay()


# Initialize and start the thermostat state machine.
tsm = TemperatureMachine()
tsm.run()


# GPIOZero creates interrupt-driven callbacks for all three buttons.
greenButton = Button(24, bounce_time=0.1)
greenButton.when_pressed = tsm.processTempStateButton

redButton = Button(25, bounce_time=0.1)
redButton.when_pressed = tsm.processTempIncButton

blueButton = Button(12, bounce_time=0.1)
blueButton.when_pressed = tsm.processTempDecButton


try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    print("Cleaning up. Exiting...")
    tsm.endDisplay = True
    redLight.off()
    blueLight.off()
    sleep(1.2)
    greenButton.close()
    redButton.close()
    blueButton.close()
    redLight.close()
    blueLight.close()
    ser.close()
