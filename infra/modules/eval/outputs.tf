output "eval_runner_arn" {
  value = aws_lambda_function.fn["eval-runner"].arn
}
output "eval_aggregator_arn" {
  value = aws_lambda_function.fn["eval-aggregator"].arn
}
output "eval_judge_arn" {
  value = aws_lambda_function.fn["eval-judge"].arn
}
output "eval_trajectory_arn" {
  value = aws_lambda_function.fn["eval-trajectory"].arn
}
output "online_eval_arn" {
  value = aws_lambda_function.fn["online-eval"].arn
}
output "bedrock_eval_role_arn" {
  value = aws_iam_role.bedrock_eval.arn
}
