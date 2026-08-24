module counter(input wire clk, output reg [1:0] value);
  always @(posedge clk) value <= value + 1'b1;
endmodule
